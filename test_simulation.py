"""
test_simulation.py
==================
Prueba integral del sistema SIN necesitar Moodle, Redis ni InfluxDB.

Simula el flujo completo:
  1. Genera series temporales sinteticas con patron real de uso de Moodle
  2. Inyecta anomalias conocidas en posiciones especificas
  3. Ejecuta los tres modelos: ARIMA, LSTM, Isolation Forest
  4. Aplica la fusion ponderada de resultados (RF6)
  5. Evalua las alertas con umbrales adaptativos (RF7)
  6. Muestra resultados en consola y guarda CSV con metricas

Uso:
    python test_simulation.py
    python test_simulation.py --samples 500   # mas muestras
    python test_simulation.py --no-lstm       # omitir LSTM (mas rapido)
"""

import argparse
import csv
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

# ── Configuracion de logging ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("simulation")

# ── Importar modulos del proyecto ──────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

from models.arima_model      import ARIMAModel
from models.lstm_model       import LSTMModel
from models.anomaly_detector import AnomalyDetector
from utils.config_loader     import load_config


# ══════════════════════════════════════════════════════════════════════════
# 1. GENERADOR DE DATOS SINTETICOS
# ══════════════════════════════════════════════════════════════════════════

def generate_moodle_timeseries(
    n_samples: int = 300,
    seed: int = 42,
    anomaly_fraction: float = 0.05,
) -> pd.DataFrame:
    """
    Genera una serie temporal que imita el comportamiento real del trafico
    de Moodle: patron diario, semanal y ruido gaussiano.

    Las anomalias se inyectan como picos abruptos o caidas bruscas.
    """
    rng = np.random.default_rng(seed)
    start = datetime(2026, 1, 12, 8, 0, 0, tzinfo=timezone.utc)   # Inicio semestre 2

    timestamps, request_counts, unique_users, error_counts, course_counts = [], [], [], [], []
    anomaly_flags = []

    # Posiciones donde se inyectan anomalias
    n_anomalies  = max(1, int(n_samples * anomaly_fraction))
    anomaly_idxs = set(rng.choice(range(50, n_samples), size=n_anomalies, replace=False))

    HOURLY = {                              # factor de carga por hora
        0: 0.05, 1: 0.03, 2: 0.02, 3: 0.02, 4: 0.02,
        5: 0.03, 6: 0.08, 7: 0.20, 8: 0.55, 9: 0.85,
        10: 1.00, 11: 0.95, 12: 0.70, 13: 0.75, 14: 0.95,
        15: 1.00, 16: 0.90, 17: 0.85, 18: 0.70, 19: 0.60,
        20: 0.45, 21: 0.30, 22: 0.15, 23: 0.07,
    }
    WEEKLY = {0: 1.0, 1: 1.1, 2: 1.15, 3: 1.1, 4: 0.9, 5: 0.4, 6: 0.2}
    BASE_REQUESTS = 800

    for i in range(n_samples):
        ts = start + timedelta(minutes=i * 5)     # ventanas de 5 min
        h  = ts.hour
        wd = ts.weekday()

        factor    = HOURLY.get(h, 0.5) * WEEKLY.get(wd, 1.0)
        req_base  = int(BASE_REQUESTS * factor)
        is_anomaly = i in anomaly_idxs

        if is_anomaly:
            anomaly_type = rng.choice(["spike", "drop", "error_burst"])
            if anomaly_type == "spike":
                req   = req_base * rng.uniform(4, 8)   # pico de trafico
                users = int(req * rng.uniform(0.3, 0.7))
                errs  = int(rng.uniform(5, 30))
                crs   = int(rng.uniform(20, 60))
            elif anomaly_type == "drop":
                req   = req_base * rng.uniform(0.02, 0.1)  # caida
                users = max(0, int(req * rng.uniform(0.1, 0.3)))
                errs  = int(rng.uniform(0, 3))
                crs   = int(rng.uniform(0, 5))
            else:  # error_burst
                req   = req_base * rng.uniform(0.8, 1.2)
                users = int(req * rng.uniform(0.2, 0.4))
                errs  = int(req * rng.uniform(0.3, 0.8))   # rafaga de errores
                crs   = int(rng.uniform(1, 10))
        else:
            noise = rng.normal(1.0, 0.15)
            req   = max(0, int(req_base * noise))
            users = max(0, int(req * rng.uniform(0.15, 0.35)))
            errs  = max(0, int(rng.poisson(max(0, req * 0.01))))
            crs   = max(0, int(rng.uniform(1, min(30, users + 1))))

        timestamps.append(int(ts.timestamp()))
        request_counts.append(float(req))
        unique_users.append(float(users))
        error_counts.append(float(errs))
        course_counts.append(float(crs))
        anomaly_flags.append(is_anomaly)

    df = pd.DataFrame({
        "timestamp":     timestamps,
        "datetime":      [datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") for t in timestamps],
        "request_count": request_counts,
        "unique_users":  unique_users,
        "error_count":   error_counts,
        "course_count":  course_counts,
        "true_anomaly":  anomaly_flags,
    })
    return df


# ══════════════════════════════════════════════════════════════════════════
# 2. EVALUACION DE TODOS LOS MODELOS
# ══════════════════════════════════════════════════════════════════════════

def run_pipeline(df: pd.DataFrame, config: dict, use_lstm: bool = True) -> pd.DataFrame:
    """
    Ejecuta el pipeline completo sobre el DataFrame simulado y devuelve
    un DataFrame con los resultados de cada modelo por cada punto.
    """
    arima   = ARIMAModel(config)
    lstm    = LSTMModel(config) if use_lstm else None
    iforest = AnomalyDetector(config)

    results = []
    total = len(df)

    for i, row in df.iterrows():
        ts    = int(row["timestamp"])
        point = {
            "request_count": row["request_count"],
            "unique_users":  row["unique_users"],
            "error_count":   row["error_count"],
            "course_count":  row["course_count"],
        }

        # Ejecutar modelos
        arima_res  = arima.update(ts, point["request_count"])
        lstm_res   = lstm.update(point) if lstm else {"is_anomaly": False, "reconstruction_error": 0.0, "threshold": None, "model": "LSTM", "note": "omitido"}
        iforest_res = iforest.update(point)

        # Fusion
        fusion = AnomalyDetector.fuse(iforest_res, arima_res, lstm_res)

        results.append({
            "datetime":         row["datetime"],
            "timestamp":        ts,
            "request_count":    point["request_count"],
            "unique_users":     point["unique_users"],
            "error_count":      point["error_count"],
            "true_anomaly":     row["true_anomaly"],
            # Por modelo
            "arima_anomaly":    arima_res.get("is_anomaly", False),
            "lstm_anomaly":     lstm_res.get("is_anomaly", False),
            "if_anomaly":       iforest_res.get("is_anomaly", False),
            "if_score":         iforest_res.get("score", 0.0),
            # Fusion
            "final_score":      fusion["final_score"],
            "final_anomaly":    fusion["is_anomaly"],
        })

        # Progreso cada 50 muestras
        if (i + 1) % 50 == 0 or (i + 1) == total:
            logger.info("  Procesando... %d/%d (%.0f%%)", i + 1, total, (i + 1) / total * 100)

    return pd.DataFrame(results)


# ══════════════════════════════════════════════════════════════════════════
# 3. METRICAS DE EVALUACION
# ══════════════════════════════════════════════════════════════════════════

def evaluate_metrics(results: pd.DataFrame, model_col: str) -> dict:
    """Calcula precision, recall y F1 comparando deteccion vs anomalia real."""
    y_true = results["true_anomaly"].astype(bool)
    y_pred = results[model_col].astype(bool)

    tp = int(( y_pred &  y_true).sum())
    fp = int(( y_pred & ~y_true).sum())
    fn = int((~y_pred &  y_true).sum())
    tn = int((~y_pred & ~y_true).sum())

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return {"TP": tp, "FP": fp, "FN": fn, "TN": tn,
            "Precision": round(precision, 3), "Recall": round(recall, 3), "F1": round(f1, 3)}


# ══════════════════════════════════════════════════════════════════════════
# 4. REPORTE FINAL EN CONSOLA
# ══════════════════════════════════════════════════════════════════════════

def print_report(results: pd.DataFrame):
    separator = "=" * 70

    print(f"\n{separator}")
    print("  REPORTE DE EVALUACION - Sistema de Deteccion de Anomalias")
    print(f"{separator}")

    # Estadisticas del dataset
    total    = len(results)
    n_true   = int(results["true_anomaly"].sum())
    print(f"\n  Dataset: {total} ventanas temporales | Anomalias reales: {n_true} ({n_true/total*100:.1f}%)")

    # Metricas por modelo
    models = {
        "ARIMA":            "arima_anomaly",
        "LSTM":             "lstm_anomaly",
        "Isolation Forest": "if_anomaly",
        "Fusion (final)":   "final_anomaly",
    }

    print(f"\n{'Modelo':<22} {'TP':>4} {'FP':>4} {'FN':>4} {'TN':>4}  {'Precision':>10} {'Recall':>8} {'F1':>6}")
    print("-" * 70)
    for name, col in models.items():
        m = evaluate_metrics(results, col)
        print(f"  {name:<20} {m['TP']:>4} {m['FP']:>4} {m['FN']:>4} {m['TN']:>4}"
              f"  {m['Precision']:>10.3f} {m['Recall']:>8.3f} {m['F1']:>6.3f}")

    # Anomalias detectadas por la fusion
    detected = results[results["final_anomaly"] & results["true_anomaly"]]
    missed   = results[~results["final_anomaly"] & results["true_anomaly"]]
    false_p  = results[results["final_anomaly"] & ~results["true_anomaly"]]

    print(f"\n  Anomalias correctamente detectadas ({len(detected)}/{n_true}):")
    for _, r in detected.iterrows():
        print(f"    [OK]  {r['datetime']}  req={r['request_count']:.0f}  score={r['final_score']:.3f}")

    if not missed.empty:
        print(f"\n  No detectadas (falsos negativos: {len(missed)}):")
        for _, r in missed.iterrows():
            print(f"    [--]  {r['datetime']}  req={r['request_count']:.0f}  score={r['final_score']:.3f}")

    if not false_p.empty:
        print(f"\n  Falsas alarmas (falsos positivos: {len(false_p)}):")
        for _, r in false_p.head(5).iterrows():
            print(f"    [!]   {r['datetime']}  req={r['request_count']:.0f}  score={r['final_score']:.3f}")

    print(f"\n{separator}\n")


# ══════════════════════════════════════════════════════════════════════════
# 5. PUNTO DE ENTRADA
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Simulacion del sistema de deteccion de anomalias Moodle")
    parser.add_argument("--samples",  type=int,  default=300, help="Numero de ventanas temporales a simular")
    parser.add_argument("--no-lstm",  action="store_true",    help="Omitir modelo LSTM (ejecucion mas rapida)")
    parser.add_argument("--output",   type=str,  default="logs/simulation_results.csv", help="Archivo CSV de salida")
    args = parser.parse_args()

    os.makedirs("logs",         exist_ok=True)
    os.makedirs("models/saved", exist_ok=True)

    config = load_config("config/config.yaml")

    # ── 1. Generar datos ────────────────────────────────────────────────
    logger.info("Generando %d ventanas temporales sinteticas...", args.samples)
    df = generate_moodle_timeseries(n_samples=args.samples)
    n_anomalies = int(df["true_anomaly"].sum())
    logger.info("Dataset generado: %d puntos, %d anomalias reales.", len(df), n_anomalies)

    # ── 2. Ejecutar pipeline de modelos ─────────────────────────────────
    use_lstm = not args.no_lstm
    logger.info("Ejecutando pipeline (LSTM=%s)...", use_lstm)
    t0 = time.time()
    results = run_pipeline(df, config, use_lstm=use_lstm)
    elapsed = time.time() - t0
    logger.info("Pipeline completado en %.1f segundos.", elapsed)

    # ── 3. Guardar CSV ──────────────────────────────────────────────────
    results.to_csv(args.output, index=False, encoding="utf-8")
    logger.info("Resultados guardados en: %s", args.output)

    # ── 4. Mostrar reporte ──────────────────────────────────────────────
    print_report(results)


if __name__ == "__main__":
    main()
