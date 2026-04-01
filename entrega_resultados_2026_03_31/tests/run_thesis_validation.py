"""
Validacion de indicadores de tesis para el sistema de deteccion de anomalias.

Variable Independiente:
- Sistema de deteccion con umbrales dinamicos contextualizados.

Variable Dependiente:
- Seguridad operacional del EVA Moodle, evaluada por metricas de rendimiento.

Uso:
    python tests/run_thesis_validation.py
    python tests/run_thesis_validation.py --records 1200
    python tests/run_thesis_validation.py --no-lstm
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import tracemalloc
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np

from alerts.alert_manager import AlertManager
from models.anomaly_detector import AnomalyDetector, MODEL_WEIGHTS
from models.arima_model import ARIMAModel
from utils.config_loader import load_config

try:
    from models.lstm_model import LSTMModel
except Exception:
    LSTMModel = None


ACCEPTANCE_DEFAULTS = {
    "max_avg_detection_ms": 150.0,
    "max_avg_end_to_end_latency_ms": 200.0,
    "max_p95_latency_ms": 300.0,
    "max_processing_time_per_record_ms": 200.0,
    "min_throughput_records_per_sec": 8.0,
    "max_peak_memory_kib": 65536.0,
}


def generate_timeseries(n: int, seed: int = 42):
    """Genera serie sintetica con estacionalidad diaria y anomalias controladas."""
    rng = np.random.default_rng(seed)
    base_ts = int(datetime(2026, 1, 12, 8, 0, tzinfo=timezone.utc).timestamp())

    rows = []
    anomaly_positions = set(range(max(100, n // 3), n, max(80, n // 15)))

    for i in range(n):
        seasonal = 200 * np.sin(2 * np.pi * i / 24)
        req = max(20.0, 420 + seasonal + float(rng.normal(0, 25)))
        users = max(5.0, req / 8 + float(rng.normal(0, 5)))
        errs = max(0.0, float(rng.normal(4, 2)))
        courses = max(1.0, float(rng.integers(6, 25)))

        is_true_anomaly = i in anomaly_positions
        if is_true_anomaly:
            req *= float(rng.uniform(4.5, 8.0))
            errs += float(rng.uniform(30, 120))
            users = max(2.0, users * float(rng.uniform(0.1, 0.5)))
            courses = max(1.0, courses * float(rng.uniform(0.2, 0.7)))

        rows.append(
            {
                "timestamp": base_ts + i * 60,
                "point": {
                    "request_count": float(req),
                    "unique_users": float(users),
                    "error_count": float(errs),
                    "course_count": float(courses),
                },
                "true_anomaly": is_true_anomaly,
            }
        )

    return rows


def evaluate_indicator_model_configuration(config: dict) -> dict:
    """Verifica parametros ARIMA-LSTM y estrategia de fusion."""
    arima_cfg = config["models"]["arima"]
    lstm_cfg = config["models"]["lstm"]

    arima = ARIMAModel(config)
    detector = AnomalyDetector(config)

    checks = {
        "arima_order_ok": tuple(arima_cfg["order"]) == arima.order,
        "arima_seasonal_ok": tuple(arima_cfg["seasonal_order"]) == arima.seasonal_order,
        "lstm_sequence_length_ok": int(lstm_cfg.get("sequence_length", 0)) > 0,
        "lstm_hidden_units_ok": int(lstm_cfg.get("hidden_units", 0)) > 0,
        "fusion_weights_ok": len(MODEL_WEIGHTS) == 3 and all(v > 0 for v in MODEL_WEIGHTS.values()),
    }

    # Validacion funcional de fusion ponderada con votos mixtos.
    fusion = AnomalyDetector.fuse(
        {"is_anomaly": True},
        {"is_anomaly": False},
        {"is_anomaly": True},
    )
    checks["fusion_output_ok"] = (
        "final_score" in fusion
        and "votes" in fusion
        and 0.0 <= float(fusion["final_score"]) <= 1.0
    )

    checks["all_pass"] = all(checks.values())
    return checks


def evaluate_indicator_contextualization(config: dict) -> dict:
    """Valida reglas de calendarizacion y ajustes por periodos academicos."""
    manager = AlertManager(config)

    # Fechas ya contempladas en el calendario de ejemplo del proyecto.
    ts_exam = int(datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc).timestamp())
    ts_normal = int(datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc).timestamp())
    ts_recess = int(datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc).timestamp())

    thr_exam = manager._adaptive_threshold(ts_exam)
    thr_normal = manager._adaptive_threshold(ts_normal)
    thr_recess = manager._adaptive_threshold(ts_recess)

    checks = {
        "calendar_loaded_ok": bool(manager.calendar.get("periods")),
        "exam_threshold_higher_ok": thr_exam >= thr_normal,
        "recess_threshold_lower_ok": thr_recess <= thr_normal,
        "adaptive_threshold_range_ok": 0.2 <= thr_exam <= 0.8 and 0.2 <= thr_recess <= 0.8,
    }

    # Sensibilidad por tipo de evento academico: se verifica al menos
    # dos comportamientos distintos de umbral segun periodo.
    checks["sensitivity_matrix_proxy_ok"] = len({thr_exam, thr_normal, thr_recess}) >= 2
    checks["all_pass"] = all(checks.values())

    return {
        **checks,
        "threshold_exam": thr_exam,
        "threshold_normal": thr_normal,
        "threshold_recess": thr_recess,
    }


def run_realtime_pipeline_benchmark(config: dict, records: int, use_lstm: bool) -> dict:
    """
    Mide indicadores de rendimiento y procesamiento en tiempo real:
    - tiempo medio de deteccion
    - latencia end-to-end
    - tiempo por registro
    - throughput
    - uso de memoria
    - escalabilidad (comparativa de lotes)
    """
    dataset = generate_timeseries(records)

    arima = ARIMAModel(config)
    detector = AnomalyDetector(config)
    alert_manager = AlertManager(config)

    # En benchmark evitamos IO para medir procesamiento puro y reducir flakiness.
    arima._save = lambda: None
    detector._save = lambda: None

    lstm = None
    if use_lstm and LSTMModel is not None:
        lstm = LSTMModel(config)
        lstm._save = lambda: None

    # Calentamiento para reducir sesgo de primera inferencia.
    warmup_n = min(120, len(dataset) // 4)
    for row in dataset[:warmup_n]:
        ts = int(row["timestamp"])
        p = row["point"]
        arima_res = arima.update(ts, p["request_count"])
        lstm_res = (
            lstm.update(p)
            if lstm is not None
            else {"is_anomaly": False, "reconstruction_error": 0.0, "threshold": None}
        )
        if_res = detector.update(p)
        fusion = AnomalyDetector.fuse(if_res, arima_res, lstm_res)
        alert_manager.evaluate(ts, p, fusion, arima_res, lstm_res)

    latencies_ms = []
    detection_ms = []

    tracemalloc.start()
    t_global_start = time.perf_counter()

    detected = 0
    true_anomalies = 0

    for row in dataset:
        ts = int(row["timestamp"])
        p = row["point"]
        true_anomalies += int(row["true_anomaly"])

        t0 = time.perf_counter()
        arima_res = arima.update(ts, p["request_count"])
        lstm_res = (
            lstm.update(p)
            if lstm is not None
            else {"is_anomaly": False, "reconstruction_error": 0.0, "threshold": None}
        )
        if_res = detector.update(p)
        fusion = AnomalyDetector.fuse(if_res, arima_res, lstm_res)
        alert = alert_manager.evaluate(ts, p, fusion, arima_res, lstm_res)
        t1 = time.perf_counter()

        elapsed_ms = (t1 - t0) * 1000.0
        latencies_ms.append(elapsed_ms)

        if fusion.get("is_anomaly", False):
            detection_ms.append(elapsed_ms)
        if alert is not None:
            detected += 1

    t_global_end = time.perf_counter()
    current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    total_s = t_global_end - t_global_start
    throughput = records / total_s if total_s > 0 else 0.0

    p95_latency = float(np.percentile(latencies_ms, 95)) if latencies_ms else 0.0
    avg_latency = float(np.mean(latencies_ms)) if latencies_ms else 0.0
    avg_detection = float(np.mean(detection_ms)) if detection_ms else 0.0

    return {
        "records": records,
        "lstm_enabled": bool(lstm is not None),
        "window_seconds": int(config["preprocessing"].get("aggregation_window_seconds", 60)),
        "avg_detection_ms": round(avg_detection, 4),
        "avg_end_to_end_latency_ms": round(avg_latency, 4),
        "p95_latency_ms": round(p95_latency, 4),
        "avg_response_time_ms": round(avg_latency, 4),
        "processing_time_per_record_ms": round(avg_latency, 4),
        "throughput_records_per_sec": round(float(throughput), 4),
        "peak_memory_kib": round(float(peak_mem / 1024.0), 2),
        "alerts_emitted": int(detected),
        "true_anomalies": int(true_anomalies),
        "resource_current_kib": round(float(current_mem / 1024.0), 2),
    }


def evaluate_scalability(config: dict, use_lstm: bool) -> dict:
    """Evalua comportamiento al incrementar volumen de registros."""
    sizes = [400, 800, 1200]
    runs = [run_realtime_pipeline_benchmark(config, n, use_lstm) for n in sizes]

    throughputs = [r["throughput_records_per_sec"] for r in runs]
    latencies = [r["avg_end_to_end_latency_ms"] for r in runs]

    # Criterios robustos y no fragiles para diferentes equipos.
    checks = {
        "throughput_positive_ok": all(t > 0 for t in throughputs),
        "latency_positive_ok": all(l > 0 for l in latencies),
        "throughput_not_collapsing_ok": throughputs[-1] >= throughputs[0] * 0.25,
        "latency_under_1s_per_record_ok": latencies[-1] < 1000,
    }
    checks["all_pass"] = all(checks.values())

    return {
        "sizes": sizes,
        "throughputs": throughputs,
        "latencies_ms": latencies,
        **checks,
    }


def evaluate_performance_acceptance(perf: dict, criteria: dict) -> dict:
    """Evalua cumplimiento de umbrales de aceptacion por metrica."""
    checks = {
        "avg_detection_ms_ok": perf["avg_detection_ms"] <= criteria["max_avg_detection_ms"],
        "avg_end_to_end_latency_ms_ok": perf["avg_end_to_end_latency_ms"] <= criteria["max_avg_end_to_end_latency_ms"],
        "p95_latency_ms_ok": perf["p95_latency_ms"] <= criteria["max_p95_latency_ms"],
        "processing_time_per_record_ms_ok": perf["processing_time_per_record_ms"] <= criteria["max_processing_time_per_record_ms"],
        "throughput_records_per_sec_ok": perf["throughput_records_per_sec"] >= criteria["min_throughput_records_per_sec"],
        "peak_memory_kib_ok": perf["peak_memory_kib"] <= criteria["max_peak_memory_kib"],
    }
    checks["all_pass"] = all(checks.values())
    return checks


def build_thesis_markdown_table(
    criteria: dict,
    perf: dict,
    perf_acceptance: dict,
    ind_model: dict,
    ind_context: dict,
    scale: dict,
    overall_ok: bool,
) -> str:
    """Construye tabla en Markdown para incluir directamente en la tesis."""
    rows = [
        ("Configuracion del modelo", "PASS", "PASS" if ind_model["all_pass"] else "FAIL"),
        ("Contextualizacion academica", "PASS", "PASS" if ind_context["all_pass"] else "FAIL"),
        ("Escalabilidad", "PASS", "PASS" if scale["all_pass"] else "FAIL"),
        (
            "Tiempo medio de deteccion (ms)",
            f"<= {criteria['max_avg_detection_ms']:.2f}",
            f"{perf['avg_detection_ms']:.4f} ({'PASS' if perf_acceptance['avg_detection_ms_ok'] else 'FAIL'})",
        ),
        (
            "Latencia end-to-end promedio (ms)",
            f"<= {criteria['max_avg_end_to_end_latency_ms']:.2f}",
            f"{perf['avg_end_to_end_latency_ms']:.4f} ({'PASS' if perf_acceptance['avg_end_to_end_latency_ms_ok'] else 'FAIL'})",
        ),
        (
            "Latencia p95 (ms)",
            f"<= {criteria['max_p95_latency_ms']:.2f}",
            f"{perf['p95_latency_ms']:.4f} ({'PASS' if perf_acceptance['p95_latency_ms_ok'] else 'FAIL'})",
        ),
        (
            "Tiempo por registro (ms)",
            f"<= {criteria['max_processing_time_per_record_ms']:.2f}",
            f"{perf['processing_time_per_record_ms']:.4f} ({'PASS' if perf_acceptance['processing_time_per_record_ms_ok'] else 'FAIL'})",
        ),
        (
            "Throughput (reg/s)",
            f">= {criteria['min_throughput_records_per_sec']:.2f}",
            f"{perf['throughput_records_per_sec']:.4f} ({'PASS' if perf_acceptance['throughput_records_per_sec_ok'] else 'FAIL'})",
        ),
        (
            "Memoria pico (KiB)",
            f"<= {criteria['max_peak_memory_kib']:.2f}",
            f"{perf['peak_memory_kib']:.2f} ({'PASS' if perf_acceptance['peak_memory_kib_ok'] else 'FAIL'})",
        ),
    ]

    lines = [
        "# Tabla de validacion para tesis",
        "",
        "| Indicador | Criterio de aceptacion | Resultado observado |",
        "|---|---:|---:|",
    ]
    for indicator, criterion, result in rows:
        lines.append(f"| {indicator} | {criterion} | {result} |")

    lines.extend(
        [
            "",
            f"Resultado global del experimento: {'PASS' if overall_ok else 'FAIL'}.",
        ]
    )
    return "\n".join(lines)


def save_text(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def save_report(path: str, rows: list[dict]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not rows:
        return

    keys = []
    for row in rows:
        for k in row.keys():
            if k not in keys:
                keys.append(k)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Validacion de indicadores de tesis")
    parser.add_argument("--records", type=int, default=1000, help="Numero de registros para benchmark principal")
    parser.add_argument("--no-lstm", action="store_true", help="Omitir LSTM aunque TensorFlow este disponible")
    parser.add_argument("--output", type=str, default="logs/thesis_validation_report.csv", help="Ruta CSV del reporte")
    parser.add_argument("--md-output", type=str, default="logs/thesis_results_table.md", help="Ruta de la tabla Markdown para tesis")
    parser.add_argument("--max-avg-detection-ms", type=float, default=ACCEPTANCE_DEFAULTS["max_avg_detection_ms"], help="Umbral maximo de tiempo medio de deteccion")
    parser.add_argument("--max-avg-e2e-latency-ms", type=float, default=ACCEPTANCE_DEFAULTS["max_avg_end_to_end_latency_ms"], help="Umbral maximo de latencia end-to-end promedio")
    parser.add_argument("--max-p95-latency-ms", type=float, default=ACCEPTANCE_DEFAULTS["max_p95_latency_ms"], help="Umbral maximo de latencia p95")
    parser.add_argument("--max-processing-ms", type=float, default=ACCEPTANCE_DEFAULTS["max_processing_time_per_record_ms"], help="Umbral maximo de tiempo por registro")
    parser.add_argument("--min-throughput-rps", type=float, default=ACCEPTANCE_DEFAULTS["min_throughput_records_per_sec"], help="Umbral minimo de throughput (reg/s)")
    parser.add_argument("--max-peak-memory-kib", type=float, default=ACCEPTANCE_DEFAULTS["max_peak_memory_kib"], help="Umbral maximo de memoria pico")
    args = parser.parse_args()

    criteria = {
        "max_avg_detection_ms": float(args.max_avg_detection_ms),
        "max_avg_end_to_end_latency_ms": float(args.max_avg_e2e_latency_ms),
        "max_p95_latency_ms": float(args.max_p95_latency_ms),
        "max_processing_time_per_record_ms": float(args.max_processing_ms),
        "min_throughput_records_per_sec": float(args.min_throughput_rps),
        "max_peak_memory_kib": float(args.max_peak_memory_kib),
    }

    config = load_config("config/config.yaml")

    print("=" * 78)
    print("VALIDACION DE TESIS - DETECCION DE ANOMALIAS CONTEXTUALIZADA")
    print("=" * 78)
    print(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    ind_model = evaluate_indicator_model_configuration(config)
    ind_context = evaluate_indicator_contextualization(config)
    perf = run_realtime_pipeline_benchmark(config, args.records, use_lstm=not args.no_lstm)
    scale = evaluate_scalability(config, use_lstm=not args.no_lstm)
    perf_acceptance = evaluate_performance_acceptance(perf, criteria)

    print("\n[Variable Independiente] Sistema de deteccion con umbrales dinamicos")
    print(f"- Configuracion del modelo: {'PASS' if ind_model['all_pass'] else 'FAIL'}")
    print(f"- Contextualizacion academica: {'PASS' if ind_context['all_pass'] else 'FAIL'}")
    print(f"- Ventana temporal configurada (s): {perf['window_seconds']}")

    print("\n[Variable Dependiente] Seguridad operacional del EVA")
    print(f"- Tiempo medio de deteccion (ms): {perf['avg_detection_ms']}")
    print(f"- Latencia end-to-end promedio (ms): {perf['avg_end_to_end_latency_ms']}")
    print(f"- Latencia p95 (ms): {perf['p95_latency_ms']}")
    print(f"- Tiempo por registro (ms): {perf['processing_time_per_record_ms']}")
    print(f"- Throughput (reg/s): {perf['throughput_records_per_sec']}")
    print(f"- Memoria pico (KiB): {perf['peak_memory_kib']}")
    print(f"- Escalabilidad: {'PASS' if scale['all_pass'] else 'FAIL'}")

    print("\n[Criterios de aceptacion del capitulo de resultados]")
    print(f"- Tiempo medio de deteccion <= {criteria['max_avg_detection_ms']:.2f} ms: {'PASS' if perf_acceptance['avg_detection_ms_ok'] else 'FAIL'}")
    print(f"- Latencia end-to-end promedio <= {criteria['max_avg_end_to_end_latency_ms']:.2f} ms: {'PASS' if perf_acceptance['avg_end_to_end_latency_ms_ok'] else 'FAIL'}")
    print(f"- Latencia p95 <= {criteria['max_p95_latency_ms']:.2f} ms: {'PASS' if perf_acceptance['p95_latency_ms_ok'] else 'FAIL'}")
    print(f"- Tiempo por registro <= {criteria['max_processing_time_per_record_ms']:.2f} ms: {'PASS' if perf_acceptance['processing_time_per_record_ms_ok'] else 'FAIL'}")
    print(f"- Throughput >= {criteria['min_throughput_records_per_sec']:.2f} reg/s: {'PASS' if perf_acceptance['throughput_records_per_sec_ok'] else 'FAIL'}")
    print(f"- Memoria pico <= {criteria['max_peak_memory_kib']:.2f} KiB: {'PASS' if perf_acceptance['peak_memory_kib_ok'] else 'FAIL'}")

    summary = {
        "model_indicator_pass": ind_model["all_pass"],
        "context_indicator_pass": ind_context["all_pass"],
        "performance_acceptance_pass": perf_acceptance["all_pass"],
        "scalability_pass": scale["all_pass"],
    }
    overall_ok = all(summary.values())
    print(f"\nResultado global: {'PASS' if overall_ok else 'FAIL'}")

    md_table = build_thesis_markdown_table(
        criteria,
        perf,
        perf_acceptance,
        ind_model,
        ind_context,
        scale,
        overall_ok,
    )

    rows = [
        {"section": "model_configuration", **ind_model},
        {"section": "academic_context", **ind_context},
        {"section": "performance", **perf},
        {"section": "acceptance_criteria", **criteria},
        {"section": "performance_acceptance", **perf_acceptance},
        {"section": "scalability", **scale},
        {"section": "summary", **summary, "overall_ok": overall_ok},
    ]
    save_report(args.output, rows)
    save_text(args.md_output, md_table)
    print(f"Reporte CSV guardado en: {args.output}")
    print(f"Tabla Markdown guardada en: {args.md_output}")

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
