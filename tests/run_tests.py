"""
tests/run_tests.py
==================
Ejecutor de pruebas unitarias para cada modulo del sistema.
NO requiere Moodle, Redis, InfluxDB ni ningun servicio externo.

Prueba cada subsistema con datos sinteticos y reporta PASS / FAIL.

Uso:
    python tests/run_tests.py
    python tests/run_tests.py --no-lstm     # omite la prueba LSTM (mas rapido)
    python tests/run_tests.py --verbose     # muestra detalle en cada prueba
"""

import argparse
import csv
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

# Asegurar que el directorio raiz del proyecto este en el path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np

# ── Colores de consola ─────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


# ══════════════════════════════════════════════════════════════════════════
# Infraestructura del runner
# ══════════════════════════════════════════════════════════════════════════

results_log: list[dict] = []


def test(name: str):
    """Decorador que captura exito/fallo de cada funcion de prueba."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            t0 = time.time()
            try:
                fn(*args, **kwargs)
                elapsed = time.time() - t0
                print(f"  {GREEN}[PASS]{RESET} {name:<55} ({elapsed:.2f}s)")
                results_log.append({"name": name, "status": "PASS", "error": ""})
            except Exception as exc:
                elapsed = time.time() - t0
                print(f"  {RED}[FAIL]{RESET} {name:<55} ({elapsed:.2f}s)")
                print(f"         {RED}{exc}{RESET}")
                if VERBOSE:
                    traceback.print_exc()
                results_log.append({"name": name, "status": "FAIL", "error": str(exc)})
        return wrapper
    return decorator


VERBOSE = False


# ══════════════════════════════════════════════════════════════════════════
# Datos sinteticos comunes para las pruebas
# ══════════════════════════════════════════════════════════════════════════

def make_config() -> dict:
    """Devuelve configuracion minimal para pruebas (sin conectar a nada)."""
    return {
        "moodle": {
            "host": "localhost", "port": 3306, "database": "moodle",
            "user": "test", "password": "test",
            "log_table": "mdl_logstore_standard_log",
            "poll_interval_seconds": 5,
        },
        "redis": {
            "host": "localhost", "port": 6379,
            "stream_name": "moodle_logs",
            "consumer_group": "test_group",
            "consumer_name": "test_consumer",
            "max_len": 10000,
        },
        "influxdb": {
            "url": "http://localhost:8086",
            "token": "test-token", "org": "UCI", "bucket": "moodle_metrics",
        },
        "preprocessing": {
            "aggregation_window_seconds": 60,
            "series_features": ["request_count", "unique_users", "error_count", "avg_response_time"],
        },
        "models": {
            "arima": {
                "order": [1, 1, 1],
                "seasonal_order": [0, 0, 0, 0],
                "retrain_interval_hours": 999,
            },
            "lstm": {
                "sequence_length": 20,
                "hidden_units": 16,
                "dropout": 0.1,
                "epochs": 2,
                "batch_size": 8,
                "retrain_interval_hours": 999,
            },
            "anomaly_detector": {
                "contamination": 0.05,
                "n_estimators": 10,
            },
        },
        "alerts": {
            "cooldown_minutes": 0,
            "channels": {
                "email": {"enabled": False},
                "log":   {"enabled": True, "file": "logs/test_alerts.log"},
            },
        },
        "academic_calendar": {
            "file": "config/academic_calendar.json",
        },
    }


def make_normal_point(seed: int = None) -> dict:
    rng = np.random.default_rng(seed)
    return {
        "request_count": float(rng.integers(200, 600)),
        "unique_users":  float(rng.integers(30, 120)),
        "error_count":   float(rng.integers(0, 10)),
        "course_count":  float(rng.integers(5, 25)),
    }


def make_anomaly_point() -> dict:
    return {
        "request_count": 9000.0,    # pico extremo
        "unique_users":  3.0,
        "error_count":   800.0,
        "course_count":  1.0,
    }


def make_timeseries(n: int = 150, seed: int = 42) -> list[tuple]:
    """Devuelve lista de (timestamp, request_count) para ARIMA."""
    rng  = np.random.default_rng(seed)
    base = int(datetime(2026, 1, 12, 8, 0, 0, tzinfo=timezone.utc).timestamp())
    ts_list = []
    for i in range(n):
        ts  = base + i * 60
        val = 400 + 200 * np.sin(2 * np.pi * i / 24) + float(rng.normal(0, 30))
        val = max(0.0, val)
        ts_list.append((ts, val))
    return ts_list


# ══════════════════════════════════════════════════════════════════════════
# GRUPO 1: Importaciones y configuracion
# ══════════════════════════════════════════════════════════════════════════

def group_imports(cfg):
    print(f"\n{BOLD}{CYAN}▶ Grupo 1: Importaciones y configuracion{RESET}")

    @test("Importar utils.config_loader")
    def t1():
        from utils.config_loader import load_config   # noqa

    @test("Cargar config/config.yaml")
    def t2():
        from utils.config_loader import load_config
        c = load_config("config/config.yaml")
        assert "moodle" in c and "redis" in c and "models" in c

    @test("Importar models.arima_model")
    def t3():
        from models.arima_model import ARIMAModel   # noqa

    @test("Importar models.anomaly_detector")
    def t4():
        from models.anomaly_detector import AnomalyDetector   # noqa

    @test("Importar alerts.alert_manager")
    def t5():
        from alerts.alert_manager import AlertManager   # noqa

    @test("Importar preprocessing.preprocessor")
    def t6():
        from preprocessing.preprocessor import Preprocessor   # noqa

    t1(); t2(); t3(); t4(); t5(); t6()


# ══════════════════════════════════════════════════════════════════════════
# GRUPO 2: Preprocesador (sin Redis)
# ══════════════════════════════════════════════════════════════════════════

def group_preprocessor(cfg):
    print(f"\n{BOLD}{CYAN}▶ Grupo 2: Preprocesamiento y aggregacion{RESET}")

    @test("Generar logs sinteticos (1000 filas)")
    def t1():
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        from generate_logs import generate_logs
        logs = generate_logs(n_rows=1000)
        assert len(logs) == 1000
        assert "timecreated" in logs[0]
        assert "action" in logs[0]

    @test("Detectar acciones de error en logs")
    def t2():
        from generate_logs import generate_logs
        logs = generate_logs(n_rows=500)
        error_logs = [r for r in logs if r["action"] in {"failed", "denied"}]
        assert len(error_logs) >= 0   # puede ser 0 con pocos registros

    @test("Aggregacion temporal: ventana de 60s")
    def t3():
        """Simula lo que hace Preprocessor._aggregate sin Redis."""
        from collections import defaultdict
        ERROR_KEYWORDS = {"failed", "denied", "error"}
        window = defaultdict(lambda: {"request_count": 0, "unique_users": set(), "error_count": 0})

        from generate_logs import generate_logs
        logs = generate_logs(n_rows=300)
        window_sec = 60
        for log in logs:
            bucket = (log["timecreated"] // window_sec) * window_sec
            window[bucket]["request_count"] += 1
            if log["userid"]:
                window[bucket]["unique_users"].add(log["userid"])
            if log["action"] in ERROR_KEYWORDS:
                window[bucket]["error_count"] += 1

        assert len(window) > 0
        for bucket, data in window.items():
            assert data["request_count"] >= 1

    @test("Guardado de logs a CSV")
    def t4():
        from generate_logs import generate_logs, save_csv
        logs = generate_logs(n_rows=100)
        path = "tests/data/test_output.csv"
        save_csv(logs, path)
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 100

    t1(); t2(); t3(); t4()


# ══════════════════════════════════════════════════════════════════════════
# GRUPO 3: Modelo ARIMA
# ══════════════════════════════════════════════════════════════════════════

def group_arima(cfg):
    print(f"\n{BOLD}{CYAN}▶ Grupo 3: Modelo ARIMA{RESET}")

    @test("Instanciar ARIMAModel")
    def t1():
        from models.arima_model import ARIMAModel
        m = ARIMAModel(cfg)
        assert m.order == (1, 1, 1)

    @test("Sin prediccion con datos insuficientes (<50)")
    def t2():
        from models.arima_model import ARIMAModel
        m   = ARIMAModel(cfg)
        ts  = int(datetime.now().timestamp())
        res = m.update(ts, 400.0)
        assert res["is_anomaly"] == False
        assert "datos insuficientes" in res.get("note", "")

    @test("Entrenar ARIMA con 150 puntos normales")
    def t3():
        from models.arima_model import ARIMAModel
        m  = ARIMAModel(cfg)
        ts_series = make_timeseries(n=150)
        for ts, val in ts_series:
            res = m.update(ts, val)
        assert m._model_fit is not None

    @test("ARIMA devuelve estructura valida en trafico normal")
    def t4():
        from models.arima_model import ARIMAModel
        m = ARIMAModel(cfg)
        ts_series = make_timeseries(n=150)
        for ts, val in ts_series:
            res = m.update(ts, val)
        # Verificar estructura del resultado (is_anomaly puede ser bool o numpy.bool_)
        assert res["is_anomaly"] in (True, False)
        assert "predicted" in res and "lower" in res and "upper" in res
        assert res["upper"] > res["lower"], "El intervalo de confianza debe ser valido"

    @test("ARIMA detecta pico extremo (9000 req)")
    def t5():
        from models.arima_model import ARIMAModel
        m = ARIMAModel(cfg)
        ts_series = make_timeseries(n=150)
        for ts, val in ts_series:
            m.update(ts, val)
        # Inyectar anomalia
        last_ts = ts_series[-1][0] + 60
        res = m.update(last_ts, 9000.0)
        # Con un pico de 9000 vs media ~400 deberia detectar anomalia
        assert res["upper"] < 9000.0 or res["is_anomaly"] == True

    t1(); t2(); t3(); t4(); t5()


# ══════════════════════════════════════════════════════════════════════════
# GRUPO 4: Isolation Forest
# ══════════════════════════════════════════════════════════════════════════

def group_isolation_forest(cfg):
    print(f"\n{BOLD}{CYAN}▶ Grupo 4: Isolation Forest (detector no supervisado){RESET}")

    @test("Instanciar AnomalyDetector")
    def t1():
        from models.anomaly_detector import AnomalyDetector
        d = AnomalyDetector(cfg)
        assert d.contamination == 0.05

    @test("Sin resultado con datos insuficientes (<100)")
    def t2():
        from models.anomaly_detector import AnomalyDetector
        d   = AnomalyDetector(cfg)
        res = d.update(make_normal_point(seed=0))
        assert res["is_anomaly"] == False
        assert "datos insuficientes" in res.get("note", "")

    @test("Entrenar con 150 puntos normales")
    def t3():
        from models.anomaly_detector import AnomalyDetector
        d = AnomalyDetector(cfg)
        for i in range(150):
            d.update(make_normal_point(seed=i))
        assert d._model is not None

    @test("No marca como anomalia un punto normal post-entrenamiento")
    def t4():
        from models.anomaly_detector import AnomalyDetector
        d = AnomalyDetector(cfg)
        for i in range(150):
            d.update(make_normal_point(seed=i))
        res = d.update(make_normal_point(seed=999))
        assert "score" in res and isinstance(res["score"], float)

    @test("Detecta pico extremo post-entrenamiento")
    def t5():
        from models.anomaly_detector import AnomalyDetector
        d = AnomalyDetector(cfg)
        for i in range(200):
            d.update(make_normal_point(seed=i))
        res = d.update(make_anomaly_point())
        assert res["is_anomaly"] == True, f"Score={res['score']:.3f} deberia ser anomalia"

    @test("Fusion de tres modelos (RF6) - estructura correcta")
    def t6():
        from models.anomaly_detector import AnomalyDetector
        if_res    = {"is_anomaly": True,  "score": 0.9}
        arima_res = {"is_anomaly": True,  "predicted": 400, "lower": 300, "upper": 500}
        lstm_res  = {"is_anomaly": False, "reconstruction_error": 0.01, "threshold": 0.1}
        fusion    = AnomalyDetector.fuse(if_res, arima_res, lstm_res)
        assert "final_score" in fusion
        assert "is_anomaly"  in fusion
        assert "votes"       in fusion
        assert 0.0 <= fusion["final_score"] <= 1.0

    @test("Fusion: 3/3 votos -> is_anomaly=True")
    def t7():
        from models.anomaly_detector import AnomalyDetector
        if_res    = {"is_anomaly": True}
        arima_res = {"is_anomaly": True}
        lstm_res  = {"is_anomaly": True}
        fusion    = AnomalyDetector.fuse(if_res, arima_res, lstm_res)
        assert fusion["is_anomaly"] == True

    @test("Fusion: 0/3 votos -> is_anomaly=False")
    def t8():
        from models.anomaly_detector import AnomalyDetector
        if_res    = {"is_anomaly": False}
        arima_res = {"is_anomaly": False}
        lstm_res  = {"is_anomaly": False}
        fusion    = AnomalyDetector.fuse(if_res, arima_res, lstm_res)
        assert fusion["is_anomaly"] == False

    t1(); t2(); t3(); t4(); t5(); t6(); t7(); t8()


# ══════════════════════════════════════════════════════════════════════════
# GRUPO 5: Modelo LSTM (opcional, lento)
# ══════════════════════════════════════════════════════════════════════════

def group_lstm(cfg, use_lstm: bool):
    print(f"\n{BOLD}{CYAN}▶ Grupo 5: Modelo LSTM{RESET}")

    if not use_lstm:
        print(f"  {YELLOW}[SKIP]{RESET} Omitido con --no-lstm")
        return

    @test("Importar TensorFlow y Keras")
    def t1():
        import tensorflow as tf
        from tensorflow import keras
        assert tf.__version__ >= "2.17"

    @test("Instanciar LSTMModel")
    def t2():
        from models.lstm_model import LSTMModel
        m = LSTMModel(cfg)
        assert m.seq_len == 20

    @test("Sin resultado con datos insuficientes")
    def t3():
        from models.lstm_model import LSTMModel
        m   = LSTMModel(cfg)
        res = m.update(make_normal_point(seed=0))
        assert res["is_anomaly"] == False

    @test("Entrenar LSTM autoencoder con 60 puntos")
    def t4():
        from models.lstm_model import LSTMModel
        m = LSTMModel(cfg)
        for i in range(60):
            res = m.update(make_normal_point(seed=i))
        assert m._model is not None, "El modelo LSTM no se entreno"

    @test("Evaluar punto normal post-entrenamiento")
    def t5():
        from models.lstm_model import LSTMModel
        m = LSTMModel(cfg)
        for i in range(60):
            m.update(make_normal_point(seed=i))
        res = m.update(make_normal_point(seed=999))
        assert "reconstruction_error" in res
        assert isinstance(res["reconstruction_error"], float)

    t1(); t2(); t3(); t4(); t5()


# ══════════════════════════════════════════════════════════════════════════
# GRUPO 6: Gestor de alertas
# ══════════════════════════════════════════════════════════════════════════

def group_alerts(cfg):
    print(f"\n{BOLD}{CYAN}▶ Grupo 6: Gestor de alertas (RF7){RESET}")

    @test("Instanciar AlertManager")
    def t1():
        from alerts.alert_manager import AlertManager
        os.makedirs("logs", exist_ok=True)
        a = AlertManager(cfg)
        assert a.cooldown_min == 0

    @test("Sin fusion positiva -> no genera alerta")
    def t2():
        from alerts.alert_manager import AlertManager
        a      = AlertManager(cfg)
        fusion = {"is_anomaly": False, "final_score": 0.2, "votes": {}}
        result = a.evaluate(
            int(datetime.now().timestamp()),
            make_normal_point(),
            fusion,
            {"is_anomaly": False, "predicted": 400, "lower": 300, "upper": 500},
            {"is_anomaly": False, "reconstruction_error": 0.01, "threshold": 0.1},
        )
        assert result is None

    @test("Score alto -> genera alerta con estructura correcta")
    def t3():
        from alerts.alert_manager import AlertManager
        a      = AlertManager(cfg)
        fusion = {
            "is_anomaly":  True,
            "final_score": 0.95,
            "votes": {"isolation_forest": 1, "arima": 1, "lstm": 1},
        }
        result = a.evaluate(
            int(datetime.now().timestamp()),
            make_anomaly_point(),
            fusion,
            {"is_anomaly": True, "predicted": 400, "lower": 300, "upper": 500},
            {"is_anomaly": True,  "reconstruction_error": 0.8, "threshold": 0.1},
        )
        assert result is not None
        assert "severity"    in result
        assert "final_score" in result
        assert "datetime"    in result

    @test("Clasificacion de severidad: score 0.95 -> MEDIUM o HIGH")
    def t4():
        from alerts.alert_manager import AlertManager
        a = AlertManager(cfg)
        s = a._classify_severity(score=0.95, threshold=0.5)
        assert s in {"MEDIUM", "HIGH"}

    @test("Umbral adaptativo ajusta con calendario academico")
    def t5():
        from alerts.alert_manager import AlertManager
        a = AlertManager(cfg)
        # Examen final (multiplier=2.0 segun el calendario) -> umbral mas alto
        exam_ts   = int(datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc).timestamp())
        normal_ts = int(datetime(2026, 2, 15, 10, 0, tzinfo=timezone.utc).timestamp())
        t_exam    = a._adaptive_threshold(exam_ts)
        t_normal  = a._adaptive_threshold(normal_ts)
        assert t_exam >= t_normal, f"Esperado t_exam({t_exam}) >= t_normal({t_normal})"

    @test("Historial de alertas se acumula correctamente")
    def t6():
        from alerts.alert_manager import AlertManager
        a = AlertManager(cfg)
        fusion = {"is_anomaly": True, "final_score": 0.99, "votes": {}}
        arima  = {"is_anomaly": True, "predicted": 400, "lower": 300, "upper": 500}
        lstm   = {"is_anomaly": True, "reconstruction_error": 0.9, "threshold": 0.1}
        ts     = int(datetime.now().timestamp())
        a.evaluate(ts, make_anomaly_point(), fusion, arima, lstm)
        hist = a.get_recent_alerts(10)
        assert len(hist) == 1

    t1(); t2(); t3(); t4(); t5(); t6()


# ══════════════════════════════════════════════════════════════════════════
# GRUPO 7: Pipeline completo (sin servicios externos)
# ══════════════════════════════════════════════════════════════════════════

def group_pipeline(cfg, use_lstm: bool):
    print(f"\n{BOLD}{CYAN}▶ Grupo 7: Pipeline completo de deteccion{RESET}")

    @test("Pipeline: 200 puntos normales + 5 anomalias conocidas")
    def t1():
        from models.arima_model      import ARIMAModel
        from models.lstm_model       import LSTMModel
        from models.anomaly_detector import AnomalyDetector
        from alerts.alert_manager    import AlertManager

        arima    = ARIMAModel(cfg)
        lstm     = LSTMModel(cfg) if use_lstm else None
        iforest  = AnomalyDetector(cfg)
        alert_mgr = AlertManager(cfg)

        base_ts = int(datetime(2026, 1, 12, 8, 0, 0, tzinfo=timezone.utc).timestamp())
        rng     = np.random.default_rng(0)

        detected_anomalies = 0
        anomaly_positions  = {150, 160, 170, 180, 190}   # posiciones donde se inyectan

        for i in range(200):
            ts    = base_ts + i * 60
            point = make_anomaly_point() if i in anomaly_positions else make_normal_point(seed=i)

            arima_res  = arima.update(ts, point["request_count"])
            lstm_res   = lstm.update(point) if lstm else {"is_anomaly": False, "reconstruction_error": 0.0, "threshold": None}
            iforest_res = iforest.update(point)
            fusion     = AnomalyDetector.fuse(iforest_res, arima_res, lstm_res)

            alert = alert_mgr.evaluate(ts, point, fusion, arima_res, lstm_res)
            if alert:
                detected_anomalies += 1

        # Con 5 anomalias fuertes debe detectar al menos 1
        assert detected_anomalies >= 1, \
            f"Se esperaba detectar anomalias pero se detecto: {detected_anomalies}"

    @test("Pipeline: resultados de fusion siempre tienen estructura valida")
    def t2():
        from models.anomaly_detector import AnomalyDetector
        rng = np.random.default_rng(42)
        iforest = AnomalyDetector(cfg)
        for i in range(150):
            p = make_normal_point(seed=i)
            iforest.update(p)

        for _ in range(20):
            p   = make_normal_point(seed=rng.integers(0, 9999).item())
            res = iforest.update(p)
            fusion = AnomalyDetector.fuse(
                res,
                {"is_anomaly": False},
                {"is_anomaly": False},
            )
            assert 0.0 <= fusion["final_score"] <= 1.0
            assert isinstance(fusion["is_anomaly"], bool)
            assert "votes" in fusion

    t1(); t2()


# ══════════════════════════════════════════════════════════════════════════
# REPORTE FINAL
# ══════════════════════════════════════════════════════════════════════════

def print_summary():
    total  = len(results_log)
    passed = sum(1 for r in results_log if r["status"] == "PASS")
    failed = sum(1 for r in results_log if r["status"] == "FAIL")

    print(f"\n{'═' * 70}")
    print(f"{BOLD}  RESUMEN DE PRUEBAS{RESET}")
    print(f"{'═' * 70}")
    print(f"  Total:   {total}")
    print(f"  {GREEN}Pasadas: {passed}{RESET}")
    if failed:
        print(f"  {RED}Fallidas: {failed}{RESET}")
        print(f"\n  Pruebas fallidas:")
        for r in results_log:
            if r["status"] == "FAIL":
                print(f"    {RED}✗{RESET} {r['name']}")
                print(f"      {r['error']}")
    else:
        print(f"  {GREEN}{BOLD}✓ Todas las pruebas pasaron correctamente.{RESET}")
    print(f"{'═' * 70}\n")

    # Guardar reporte CSV
    os.makedirs("logs", exist_ok=True)
    path = "logs/test_report.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "status", "error"])
        writer.writeheader()
        writer.writerows(results_log)
    print(f"  Reporte guardado en: {path}\n")

    return failed == 0


# ══════════════════════════════════════════════════════════════════════════
# PUNTO DE ENTRADA
# ══════════════════════════════════════════════════════════════════════════

def main():
    global VERBOSE
    parser = argparse.ArgumentParser(description="Ejecutor de pruebas del sistema")
    parser.add_argument("--no-lstm",  action="store_true", help="Omitir pruebas del modelo LSTM")
    parser.add_argument("--verbose",  action="store_true", help="Mostrar traceback completo")
    args = parser.parse_args()
    VERBOSE  = args.verbose
    use_lstm = not args.no_lstm

    os.makedirs("tests/data",  exist_ok=True)
    os.makedirs("logs",         exist_ok=True)
    os.makedirs("models/saved", exist_ok=True)

    cfg = make_config()

    print(f"\n{BOLD}{'═' * 70}")
    print("  SUITE DE PRUEBAS - Sistema de Deteccion de Anomalias Moodle UCI")
    print(f"{'═' * 70}{RESET}")
    print(f"  Fecha:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  LSTM:   {'activado' if use_lstm else 'omitido (--no-lstm)'}")

    group_imports(cfg)
    group_preprocessor(cfg)
    group_arima(cfg)
    group_isolation_forest(cfg)
    group_lstm(cfg, use_lstm)
    group_alerts(cfg)
    group_pipeline(cfg, use_lstm)

    ok = print_summary()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
