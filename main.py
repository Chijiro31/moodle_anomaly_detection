"""
Orquestador principal del sistema de deteccion temprana de anomalias
en el Entorno Virtual de Aprendizaje Moodle (UCI).

Arquitectura hibrida implementada segun el metodo ABD:
  - Captura de logs (RF1)          -> MoodleLogCapture
  - Preprocesamiento  (RF2)        -> Preprocessor
  - Modelado ARIMA   (RF3)         -> ARIMAModel
  - Modelado LSTM    (RF4)         -> LSTMModel
  - Deteccion no supervisada (RF5) -> AnomalyDetector (Isolation Forest)
  - Fusion de modelos (RF6)        -> AnomalyDetector.fuse()
  - Alertas adaptativas (RF7)      -> AlertManager
  - Visualizacion en Grafana (RF8) -> InfluxDBWriter

Flujo de datos:
  Moodle DB -> Redis Stream (raw) -> Preprocessor -> Redis Stream (ts)
  -> AnalyticsEngine -> InfluxDB -> Grafana
"""

import json
import logging
import signal
import sys
import threading
import time
from collections import deque

import redis

from capture.log_capture         import MoodleLogCapture
from preprocessing.preprocessor  import Preprocessor
from models.arima_model          import ARIMAModel
from models.lstm_model           import LSTMModel
from models.anomaly_detector     import AnomalyDetector
from models.adaptive_fusion      import AdaptiveFusion
from alerts.alert_manager        import AlertManager
from dashboard.influx_writer     import InfluxDBWriter
from drift.concept_drift_detector import ConceptDriftDetector
from utils.config_loader         import load_config

# ------------------------------------------------------------------
# Configuracion del logging global
# ------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/system.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


# ------------------------------------------------------------------
# Motor de analisis: consuma el stream de series temporales
# ------------------------------------------------------------------

class AnalyticsEngine:
    """
    Lee los vectores de series temporales del stream 'moodle_timeseries',
    ejecuta los tres modelos, fusiona los resultados y genera alertas.
    """

    STREAM = "moodle_timeseries"
    GROUP  = "analytics_group"
    CONSUMER = "engine_1"
    DRIFT_STATUS_KEY = "moodle:drift:status"

    def __init__(self, config: dict):
        self.cfg   = config
        self._stop = threading.Event()

        self.redis_client = redis.Redis(
            host=config["redis"]["host"],
            port=config["redis"]["port"],
            decode_responses=True,
        )
        self._ensure_group()

        # Modelos analiticos
        self.arima   = ARIMAModel(config)
        self.lstm    = LSTMModel(config)
        self.iforest = AnomalyDetector(config)

        # Subsistemas de alerta y visualizacion
        self.alert_mgr   = AlertManager(config)
        self.influx      = InfluxDBWriter(config)

        # Fusion de modelos (RF6): variante adaptativa opcional, con pesos
        # iniciales identicos a los validados en la simulacion offline.
        fusion_cfg = config.get("adaptive_fusion", {})
        self.adaptive_fusion = AdaptiveFusion(fusion_cfg) if fusion_cfg.get("enabled", False) else None

        # Deteccion de concept drift (opcional): monitorea la desviacion
        # entre la prediccion de ARIMA y el trafico real observado.
        drift_cfg = config.get("drift_detection", {})
        self.drift_enabled = drift_cfg.get("enabled", False)
        if self.drift_enabled:
            ph_cfg    = drift_cfg.get("page_hinkley", {})
            adwin_cfg = drift_cfg.get("adwin", {})
            self.drift_detector = ConceptDriftDetector({
                "ph_alpha":     ph_cfg.get("alpha", 0.5),
                "ph_threshold": ph_cfg.get("threshold", 50),
                "ph_delta":     ph_cfg.get("delta", 0.005),
                "adwin_width":  max(adwin_cfg.get("widths", [100])),
                "page_hinkley": True,
                "adwin":        adwin_cfg.get("enabled", True),
            })

        # Intentar cargar modelos persistidos
        self.arima.load()
        self.lstm.load()
        self.iforest.load()

    def _ensure_group(self):
        try:
            self.redis_client.xgroup_create(
                self.STREAM, self.GROUP, id="0", mkstream=True
            )
        except redis.exceptions.ResponseError:
            pass

    # ------------------------------------------------------------------
    # Bucle principal
    # ------------------------------------------------------------------

    def start(self):
        logger.info("Motor de analisis iniciado.")
        while not self._stop.is_set():
            entries = self.redis_client.xreadgroup(
                self.GROUP,
                self.CONSUMER,
                {self.STREAM: ">"},
                count=100,
                block=2000,
            )
            if not entries:
                continue

            ids_to_ack = []
            for _, messages in entries:
                for msg_id, fields in messages:
                    self._process(fields)
                    ids_to_ack.append(msg_id)

            if ids_to_ack:
                self.redis_client.xack(self.STREAM, self.GROUP, *ids_to_ack)

    def stop(self):
        self._stop.set()
        self.influx.close()

    # ------------------------------------------------------------------
    # Procesamiento de un vector de series temporales
    # ------------------------------------------------------------------

    def _process(self, fields: dict):
        try:
            ts    = int(fields.get("timestamp", time.time()))
            point = {
                "request_count": float(fields.get("request_count", 0)),
                "unique_users":  float(fields.get("unique_users",  0)),
                "error_count":   float(fields.get("error_count",   0)),
                "course_count":  float(fields.get("course_count",  0)),
            }

            # --- RF3: ARIMA sobre request_count ---
            arima_result = self.arima.update(ts, point["request_count"])

            # --- RF4: LSTM sobre vector completo ---
            lstm_result  = self.lstm.update(point)

            # --- RF5: Isolation Forest ---
            if_result    = self.iforest.update(point)

            # --- RF6: Fusion de modelos ---
            if self.adaptive_fusion:
                fusion = self.adaptive_fusion.fuse(if_result, arima_result, lstm_result)
            else:
                fusion = AnomalyDetector.fuse(if_result, arima_result, lstm_result)

            # --- RF8: Escritura en InfluxDB ---
            self.influx.write_traffic(ts, point)
            self.influx.write_scores(ts, fusion, arima_result, lstm_result, if_result)

            # --- RF7: Gestion de alertas ---
            alert = self.alert_mgr.evaluate(ts, point, fusion, arima_result, lstm_result)
            if alert:
                self.influx.write_alert(alert)

            # --- Deteccion de concept drift (opcional) ---
            if self.drift_enabled:
                predicted = arima_result.get("predicted")
                if predicted is not None:
                    self.drift_detector.update(prediction=predicted, actual=point["request_count"])
                self._publish_drift_status()

        except Exception as exc:
            logger.error("Error en procesamiento analitico: %s", exc, exc_info=True)

    def _publish_drift_status(self):
        """Publica el estado del detector de concept drift en Redis para
        que el API server (proceso independiente) pueda consultarlo."""
        try:
            status = self.drift_detector.get_status()
            self.redis_client.set(self.DRIFT_STATUS_KEY, json.dumps(status))
        except Exception as exc:
            logger.error("Error al publicar estado de drift: %s", exc)


# ------------------------------------------------------------------
# Punto de entrada principal
# ------------------------------------------------------------------

def main():
    import os
    os.makedirs("logs", exist_ok=True)
    os.makedirs("models/saved", exist_ok=True)

    config = load_config("config/config.yaml")
    logger.info("=" * 60)
    logger.info("Sistema de Deteccion Temprana de Anomalias - UCI Moodle")
    logger.info("=" * 60)

    # Instanciar subsistemas
    capture      = MoodleLogCapture(config)
    preprocessor = Preprocessor(config)
    engine       = AnalyticsEngine(config)

    # Registrar signal handler para apagado ordenado
    def shutdown(sig, frame):
        logger.info("Sinal de parada recibida. Terminando subsistemas...")
        capture.stop()
        preprocessor.stop()
        engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Lanzar subsistemas en hilos independientes
    threads = [
        threading.Thread(target=capture.start,      name="Capture",      daemon=True),
        threading.Thread(target=preprocessor.start, name="Preprocessor", daemon=True),
        threading.Thread(target=engine.start,       name="Analytics",    daemon=True),
    ]

    for t in threads:
        t.start()
        logger.info("Hilo '%s' iniciado.", t.name)

    logger.info("Sistema en ejecucion. Presione Ctrl+C para detener.")

    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
