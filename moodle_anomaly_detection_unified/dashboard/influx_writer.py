"""
Subsistema de visualizacion: escritura en InfluxDB para Grafana (RF8).
Publica las metricas procesadas, scores de anomalia y alertas en
InfluxDB usando la API de escritura de linea (line protocol).
Grafana se conecta a InfluxDB como datasource para el dashboard.
"""

import logging
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

logger = logging.getLogger(__name__)


class InfluxDBWriter:
    """
    Escribe puntos de medicion en InfluxDB para su visualizacion
    en el dashboard de Grafana (RF8).

    Mediciones que se almacenan:
      - moodle_traffic    : metricas de trafico agregado
      - anomaly_scores    : scores de cada modelo y score final fusionado
      - alerts            : eventos de alerta generados
    """

    def __init__(self, config: dict, bucket: str = None, default_tags: dict = None):
        influx_cfg  = config["influxdb"]
        self.bucket = bucket or influx_cfg["bucket"]
        self.org    = influx_cfg["org"]
        # Tags aplicados a todos los puntos (p.ej. {"dataset": "mi_export.csv"}
        # en scripts/analyze_dataset.py, para distinguir corridas de analisis).
        self.default_tags = default_tags or {}

        self.client = InfluxDBClient(
            url=influx_cfg["url"],
            token=influx_cfg["token"],
            org=self.org,
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        logger.info("Conexion a InfluxDB establecida: %s (bucket=%s)", influx_cfg["url"], self.bucket)

    # ------------------------------------------------------------------
    # Escritura de metricas de trafico
    # ------------------------------------------------------------------

    def write_traffic(self, timestamp: int, point: dict):
        """Escribe el vector de metricas de trafico de la ventana temporal."""
        p = (
            Point("moodle_traffic")
            .time(timestamp, WritePrecision.S)
            .field("request_count", float(point.get("request_count", 0)))
            .field("unique_users",  float(point.get("unique_users",  0)))
            .field("error_count",   float(point.get("error_count",   0)))
            .field("course_count",  float(point.get("course_count",  0)))
        )
        self._write(p)

    # ------------------------------------------------------------------
    # Escritura de scores de anomalia
    # ------------------------------------------------------------------

    def write_scores(
        self,
        timestamp: int,
        fusion_result: dict,
        arima_result:  dict,
        lstm_result:   dict,
        if_result:     dict,
    ):
        """Escribe los scores individuales y el score fusionado."""
        p = (
            Point("anomaly_scores")
            .time(timestamp, WritePrecision.S)
            .field("final_score",           float(fusion_result.get("final_score", 0)))
            .field("is_anomaly",            int(fusion_result.get("is_anomaly",    False)))
            .field("arima_anomaly",         int(arima_result.get("is_anomaly",     False)))
            .field("lstm_anomaly",          int(lstm_result.get("is_anomaly",      False)))
            .field("if_score",              float(if_result.get("score",            0)))
            .field("if_anomaly",            int(if_result.get("is_anomaly",         False)))
            .field("arima_predicted",       float(arima_result.get("predicted",    0)))
            .field("arima_lower",           float(arima_result.get("lower",        0)))
            .field("arima_upper",           float(arima_result.get("upper",        0)))
            .field("lstm_recon_error",      float(lstm_result.get("reconstruction_error", 0)))
            .field("lstm_threshold",        float(lstm_result.get("threshold") or 0))
        )
        self._write(p)

    # ------------------------------------------------------------------
    # Escritura de alertas
    # ------------------------------------------------------------------

    def write_alert(self, alert: dict):
        """Escribe un evento de alerta en InfluxDB."""
        severity_map = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
        p = (
            Point("alerts")
            .time(alert["timestamp"], WritePrecision.S)
            .tag("severity", alert.get("severity", "LOW"))
            .field("severity_level", severity_map.get(alert.get("severity", "LOW"), 1))
            .field("final_score",    float(alert.get("final_score", 0)))
            .field("request_count",  float(alert.get("metrics", {}).get("request_count", 0)))
            .field("unique_users",   float(alert.get("metrics", {}).get("unique_users",  0)))
        )
        self._write(p)
        logger.info("Alerta escrita en InfluxDB: %s", alert.get("datetime"))

    # ------------------------------------------------------------------
    # Metodo interno
    # ------------------------------------------------------------------

    def _write(self, point: Point):
        try:
            for tag_key, tag_value in self.default_tags.items():
                point = point.tag(tag_key, tag_value)
            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
        except Exception as exc:
            logger.error("Error al escribir en InfluxDB: %s", exc)

    def close(self):
        self.client.close()
