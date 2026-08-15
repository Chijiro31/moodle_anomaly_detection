"""
scripts/analyze_dataset.py
===========================
Analiza un dataset de logs de Moodle proporcionado por el usuario (un
archivo CSV), en vez de conectarse en vivo a Redis Streams. Corre el
mismo pipeline analitico (ARIMA + LSTM + Isolation Forest + fusion
ponderada) y publica los resultados en un bucket de InfluxDB SEPARADO
del monitoreo en tiempo real (config["influxdb"]["batch_bucket"]), para
que se puedan visualizar en el dashboard dedicado
"Moodle Batch Dataset Analysis - UCI" sin mezclarse con la operacion en
vivo del sistema.

Formatos de entrada soportados (autodetectados por las columnas del CSV):

1. Logs crudos, mismo esquema que mdl_logstore_standard_log /
   capture/log_capture.py:
     timecreated, userid, courseid, action  (component/target/objectid/
     contextlevel/ip opcionales)
   Se agregan en ventanas de 60s con la misma logica que
   preprocessing/preprocessor.py (RF2).

2. Metricas ya agregadas por ventana:
     timestamp, request_count, unique_users, error_count, course_count
   Si ademas incluye una columna `true_anomaly` (0/1), se calculan
   precision/recall/F1 (dataset etiquetado); si no, solo se reportan
   los scores y alertas generadas (dataset real sin etiquetas).

Uso:
    python scripts/analyze_dataset.py --input mi_dataset.csv
    python scripts/analyze_dataset.py --input mi_dataset.csv --dataset-name "UCI-marzo-2026" --no-lstm
    python scripts/analyze_dataset.py --input mi_dataset.csv --no-influx   # solo CSV/consola, sin Grafana
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import pandas as pd

from models.arima_model import ARIMAModel
from models.lstm_model import LSTMModel
from models.anomaly_detector import AnomalyDetector
from preprocessing.preprocessor import ERROR_KEYWORDS
from dashboard.influx_writer import InfluxDBWriter
from utils.config_loader import load_config

RAW_LOG_COLUMNS = {"timecreated", "userid"}
AGGREGATED_COLUMNS = {"request_count", "unique_users"}


def load_dataset(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    return df


def detect_format(df: pd.DataFrame) -> str:
    cols = set(df.columns)
    if AGGREGATED_COLUMNS.issubset(cols):
        return "aggregated"
    if RAW_LOG_COLUMNS.issubset(cols):
        return "raw"
    raise ValueError(
        "Formato de CSV no reconocido. Se esperaba: "
        f"columnas agregadas {sorted(AGGREGATED_COLUMNS)} + timestamp, "
        f"o logs crudos {sorted(RAW_LOG_COLUMNS)}. "
        f"Columnas encontradas: {sorted(cols)}"
    )


def aggregate_raw_logs(df: pd.DataFrame, window_seconds: int = 60) -> pd.DataFrame:
    """Agrega logs crudos en ventanas temporales (misma logica que RF2)."""
    buckets: dict = defaultdict(lambda: {
        "request_count": 0, "unique_users": set(),
        "error_count": 0, "course_count": set(),
    })

    for _, row in df.iterrows():
        ts = int(row["timecreated"])
        bucket_ts = (ts // window_seconds) * window_seconds
        b = buckets[bucket_ts]
        b["request_count"] += 1

        userid = row.get("userid", 0)
        if userid and int(userid) != 0:
            b["unique_users"].add(userid)

        action = str(row.get("action", "")).lower()
        if any(kw in action for kw in ERROR_KEYWORDS):
            b["error_count"] += 1

        courseid = row.get("courseid", 0)
        if courseid and int(courseid) != 0:
            b["course_count"].add(courseid)

    rows = []
    for ts in sorted(buckets):
        b = buckets[ts]
        rows.append({
            "timestamp": ts,
            "request_count": float(b["request_count"]),
            "unique_users": float(len(b["unique_users"])),
            "error_count": float(b["error_count"]),
            "course_count": float(len(b["course_count"])),
        })
    return pd.DataFrame(rows)


def run_pipeline(df: pd.DataFrame, config: dict, use_lstm: bool) -> pd.DataFrame:
    arima = ARIMAModel(config)
    lstm = LSTMModel(config) if use_lstm else None
    iforest = AnomalyDetector(config)
    arima._save = lambda: None
    iforest._save = lambda: None
    if lstm is not None:
        lstm._save = lambda: None

    has_labels = "true_anomaly" in df.columns
    results = []

    for _, row in df.iterrows():
        ts = int(row["timestamp"])
        point = {
            "request_count": float(row["request_count"]),
            "unique_users": float(row["unique_users"]),
            "error_count": float(row.get("error_count", 0.0)),
            "course_count": float(row.get("course_count", 1.0)),
        }

        arima_res = arima.update(ts, point["request_count"])
        lstm_res = (
            lstm.update(point) if lstm is not None
            else {"is_anomaly": False, "reconstruction_error": 0.0, "threshold": None}
        )
        if_res = iforest.update(point)
        fusion = AnomalyDetector.fuse(if_res, arima_res, lstm_res)

        result = {
            "timestamp": ts,
            "datetime": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            **point,
            "arima_predicted": arima_res.get("predicted", 0.0),
            "arima_lower": arima_res.get("lower", 0.0),
            "arima_upper": arima_res.get("upper", 0.0),
            "arima_anomaly": bool(arima_res.get("is_anomaly", False)),
            "lstm_recon_error": lstm_res.get("reconstruction_error", 0.0),
            "lstm_threshold": lstm_res.get("threshold") or 0.0,
            "lstm_anomaly": bool(lstm_res.get("is_anomaly", False)),
            "if_score": if_res.get("score", 0.0),
            "if_anomaly": bool(if_res.get("is_anomaly", False)),
            "final_score": fusion.get("final_score", 0.0),
            "final_anomaly": bool(fusion.get("is_anomaly", False)),
        }
        if has_labels:
            result["true_anomaly"] = bool(row["true_anomaly"])
        results.append(result)

    return pd.DataFrame(results)


def compute_metrics(results: pd.DataFrame) -> dict | None:
    if "true_anomaly" not in results.columns:
        return None
    tp = int(((results.true_anomaly) & (results.final_anomaly)).sum())
    fp = int(((~results.true_anomaly) & (results.final_anomaly)).sum())
    fn = int(((results.true_anomaly) & (~results.final_anomaly)).sum())
    tn = int(((~results.true_anomaly) & (~results.final_anomaly)).sum())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"TP": tp, "FP": fp, "FN": fn, "TN": tn,
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def write_to_influx(results: pd.DataFrame, config: dict, dataset_name: str):
    bucket = config["influxdb"].get("batch_bucket", "moodle_batch_analysis")
    influx = InfluxDBWriter(config, bucket=bucket, default_tags={"dataset": dataset_name})
    try:
        for _, row in results.iterrows():
            ts = int(row["timestamp"])
            point = {
                "request_count": row["request_count"], "unique_users": row["unique_users"],
                "error_count": row["error_count"], "course_count": row["course_count"],
            }
            fusion = {"final_score": row["final_score"], "is_anomaly": row["final_anomaly"]}
            arima_res = {"is_anomaly": row["arima_anomaly"], "predicted": row["arima_predicted"],
                         "lower": row["arima_lower"], "upper": row["arima_upper"]}
            lstm_res = {"is_anomaly": row["lstm_anomaly"], "reconstruction_error": row["lstm_recon_error"],
                        "threshold": row["lstm_threshold"]}
            if_res = {"score": row["if_score"], "is_anomaly": row["if_anomaly"]}

            influx.write_traffic(ts, point)
            influx.write_scores(ts, fusion, arima_res, lstm_res, if_res)
            if row["final_anomaly"]:
                severity = "HIGH" if row.get("true_anomaly", row["final_score"] >= 0.75) else "MEDIUM"
                influx.write_alert({
                    "timestamp": ts, "severity": severity,
                    "final_score": row["final_score"], "metrics": point,
                })
    finally:
        influx.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="Ruta al CSV del dataset a analizar")
    parser.add_argument("--dataset-name", default=None,
                        help="Nombre para identificar esta corrida en el dashboard (default: nombre del archivo)")
    parser.add_argument("--window-seconds", type=int, default=None,
                        help="Ventana de agregacion en segundos si el CSV trae logs crudos (default: config.yaml)")
    parser.add_argument("--no-lstm", action="store_true", help="Omitir LSTM (mas rapido)")
    parser.add_argument("--no-influx", action="store_true", help="No escribir en InfluxDB, solo CSV/consola")
    parser.add_argument("--output", default=None, help="Ruta del CSV de resultados (default: logs/analysis_<nombre>.csv)")
    args = parser.parse_args()

    dataset_name = args.dataset_name or os.path.splitext(os.path.basename(args.input))[0]
    config = load_config("config/config.yaml")
    window_seconds = args.window_seconds or config["preprocessing"].get("aggregation_window_seconds", 60)

    print("=" * 78)
    print(f"ANALISIS DE DATASET PROPORCIONADO: {args.input}")
    print("=" * 78)

    df = load_dataset(args.input)
    fmt = detect_format(df)
    print(f"Formato detectado: {fmt} | {len(df):,} filas")

    if fmt == "raw":
        df = aggregate_raw_logs(df, window_seconds=window_seconds)
        print(f"Agregado en {len(df):,} ventanas de {window_seconds}s")

    df = df.sort_values("timestamp").reset_index(drop=True)

    t0 = time.time()
    results = run_pipeline(df, config, use_lstm=not args.no_lstm)
    elapsed = time.time() - t0
    print(f"Pipeline completado en {elapsed:.1f}s sobre {len(results):,} ventanas.")

    n_alerts = int(results["final_anomaly"].sum())
    print(f"Alertas generadas: {n_alerts} ({n_alerts / len(results) * 100:.1f}% de las ventanas)")

    metrics = compute_metrics(results)
    if metrics:
        print("\nDataset etiquetado (columna true_anomaly): metricas de deteccion")
        print(f"  TP={metrics['TP']} FP={metrics['FP']} FN={metrics['FN']} TN={metrics['TN']}")
        print(f"  Precision={metrics['precision']} Recall={metrics['recall']} F1={metrics['f1']}")
    else:
        print("\nDataset sin etiquetas (sin columna true_anomaly): no se calculan precision/recall,")
        print("solo se reportan los scores y alertas generadas por el pipeline.")

    output = args.output or f"logs/analysis_{dataset_name}.csv"
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    results.to_csv(output, index=False, encoding="utf-8")
    print(f"\nResultados guardados en: {output}")

    if not args.no_influx:
        print(f"Escribiendo en InfluxDB (bucket '{config['influxdb'].get('batch_bucket', 'moodle_batch_analysis')}', "
              f"dataset='{dataset_name}')...")
        write_to_influx(results, config, dataset_name)
        first_dt = results.iloc[0]["datetime"]
        last_dt = results.iloc[-1]["datetime"]
        print(f"Listo. Ajusta el rango de tiempo del dashboard 'Moodle Batch Dataset Analysis - UCI' "
              f"a [{first_dt} .. {last_dt}] para verlo.")


if __name__ == "__main__":
    main()
