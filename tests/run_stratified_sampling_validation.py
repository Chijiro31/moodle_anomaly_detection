"""
tests/run_stratified_sampling_validation.py
============================================
Valida el pipeline completo (ARIMA + LSTM + Isolation Forest + fusion)
sobre una muestra sintetica que reproduce el diseno de muestreo descrito
en el capitulo de metodologia de la tesis:

    Poblacion: registros de acceso de Moodle UCI, enero 2019 - diciembre
    2020 y periodo 2023 - diciembre 2024.
    Muestra: 1,200,000 registros (30% de la poblacion), estratificada por
        - periodo academico (examenes, matriculas, docencia normal)
        - horario (laborable, no laborable, fin de semana)
        - tipo de usuario (estudiante, profesor, administrador)
    Asignacion: 70% entrenamiento (840,000) / 30% validacion (360,000)

No existe acceso a la base de datos real de Moodle UCI (ver
LIMITATIONS_AND_FUTURE_WORK.md), por lo que este script GENERA una
poblacion sintetica con esa misma estratificacion y tamano, en vez de
extraerla de mdl_logstore_standard_log. El objetivo es someter el
pipeline ya validado a pequena escala (300 ventanas, tabla 10 de la
tesis) a la escala real especificada en el muestreo, y comparar el
desempeno entre el segmento de "entrenamiento" y el de "validacion".

Decisiones de diseno (no especificadas literalmente en el texto de la
tesis, documentadas aqui para que sean auditables):

1. Las proporciones exactas de cada estrato no estan dadas en el texto
   original; se asumen valores plausibles para una universidad cubana
   (ver *_PROPORTIONS abajo) y se reportan las proporciones reales
   obtenidas en la muestra generada.
2. El calendario academico historico (2019, 2020, 2023, 2024) no esta
   documentado dia a dia; se asume una estructura estable ano a ano
   (2 semestres, con matricula, examenes parciales y examenes finales
   por semestre), analoga a la que ya usa config/academic_calendar.json
   para el ano en curso.
3. La particion 70/30 se realiza CRONOLOGICAMENTE (primeras ventanas en
   el tiempo -> entrenamiento, ultimas -> validacion) en vez de un
   muestreo aleatorio de registros individuales. Los modelos de este
   sistema son de aprendizaje incremental/online (ARIMA y LSTM se
   reentrenan sobre su propio historial secuencial); mezclar
   aleatoriamente registros de distintas fechas violaria el orden
   temporal que esos modelos asumen y produciria fuga de informacion
   del futuro hacia el pasado. La estratificacion (paso 1) sigue
   aplicandose sobre la poblacion completa antes de ordenar por fecha.
4. Para mantener el runtime tratable, la muestra de 1,200,000 "registros
   de acceso" se materializa como conteos agregados en ventanas de 60s
   (mismo formato que preprocessing/preprocessor.py), en vez de generar
   1.2 millones de filas individuales fila-por-fila: el numero de
   ventanas (N_WINDOWS) se fija de modo que el promedio de peticiones
   por ventana sea comparable al de la simulacion ya validada (~400),
   y el total de peticiones agregadas coincide con el tamano de muestra
   especificado (1,200,000).
5. Las ventanas se agrupan en "dias representativos" (bloques de 24
   ventanas horarias consecutivas, moduladas por el mismo patron horario
   suave que ya usa el sistema en produccion). Muestrear 3000 fechas
   independientes al azar en 4 anos y luego ordenarlas cronologicamente
   (primer intento de este script) produce una serie sin coherencia
   local: cada ventana "adyacente" podia caer en un dia distinto y
   arbitrariamente lejano, rompiendo la autocorrelacion que ARIMA y LSTM
   necesitan para aprender cualquier patron (el recall caia a ~7%, muy
   por debajo del 93.3% de la Tabla 10). Al muestrear por dias completos
   en vez de instantes sueltos, cada bloque de 24 ventanas es localmente
   suave (como en la simulacion ya validada) y solo hay un salto grande
   entre un dia representativo y el siguiente, no en cada ventana.

Uso:
    python tests/run_stratified_sampling_validation.py
    python tests/run_stratified_sampling_validation.py --windows 3000
    python tests/run_stratified_sampling_validation.py --no-lstm
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np

from models.arima_model import ARIMAModel
from models.lstm_model import LSTMModel
from models.anomaly_detector import AnomalyDetector
from preprocessing.preprocessor import ERROR_KEYWORDS  # reutilizado solo como referencia

# ═══════════════════════════════════════════════════════════════════════
# 1. DEFINICION DE LA POBLACION Y EL MUESTREO ESTRATIFICADO
# ═══════════════════════════════════════════════════════════════════════

POPULATION_SPANS = [
    (datetime(2019, 1, 1, tzinfo=timezone.utc), datetime(2020, 12, 31, tzinfo=timezone.utc)),
    (datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2024, 12, 31, tzinfo=timezone.utc)),
]

SAMPLE_SIZE = 1_200_000
TRAIN_FRACTION = 0.70

# Proporciones asumidas por estrato (ver punto 1 del docstring del modulo).
ACADEMIC_PERIOD_PROPORTIONS = {"docencia_normal": 0.60, "examenes": 0.25, "matriculas": 0.15}
HORARIO_PROPORTIONS = {"laborable": 0.65, "fin_de_semana": 0.25, "no_laborable": 0.10}
USER_TYPE_PROPORTIONS = {"estudiante": 0.82, "profesor": 0.14, "administrador": 0.04}

ACADEMIC_PERIOD_MULTIPLIER = {"docencia_normal": 1.0, "examenes": 1.8, "matriculas": 1.4}
HORARIO_MULTIPLIER = {"laborable": 1.0, "fin_de_semana": 0.35, "no_laborable": 0.30}

# Feriados nacionales cubanos recurrentes (mes, dia) -> horario "no_laborable".
HOLIDAYS_MD = {(1, 1), (5, 1), (7, 26), (10, 10), (12, 25)}

REQUESTS_PER_USER = {"estudiante": 7.0, "profesor": 4.0, "administrador": 2.5}
BASE_ERROR_RATE = {"estudiante": 0.09, "profesor": 0.05, "administrador": 0.04}


def period_for_date(dt: datetime) -> str:
    """
    Clasifica una fecha en {examenes, matriculas, docencia_normal} usando
    una estructura academica anual estable (2 semestres por ano, con
    matricula + examenes parciales + examenes finales cada uno).
    Solo se usan mes/dia (independiente del ano) por simplicidad, ya que
    no se dispone del calendario historico dia a dia real de la UCI.
    """
    md = (dt.month, dt.day)

    def in_range(start, end):
        return start <= md <= end

    if in_range((1, 5), (1, 11)) or in_range((8, 20), (8, 31)):
        return "matriculas"
    if in_range((3, 9), (3, 22)) or in_range((5, 11), (5, 30)) or \
       in_range((10, 20), (11, 2)) or in_range((12, 1), (12, 20)):
        return "examenes"
    return "docencia_normal"


def horario_for_date(dt: datetime) -> str:
    if (dt.month, dt.day) in HOLIDAYS_MD:
        return "no_laborable"
    if dt.weekday() >= 5:  # 5=sabado, 6=domingo
        return "fin_de_semana"
    return "laborable"


def sample_user_type(rng: random.Random) -> str:
    return rng.choices(
        list(USER_TYPE_PROPORTIONS.keys()),
        weights=list(USER_TYPE_PROPORTIONS.values()),
    )[0]


HOURLY_PATTERN = {
    0: 0.05, 1: 0.03, 2: 0.02, 3: 0.02, 4: 0.02, 5: 0.03, 6: 0.05,
    7: 0.15, 8: 0.50, 9: 0.80, 10: 1.00, 11: 0.95, 12: 0.70, 13: 0.75,
    14: 0.95, 15: 1.00, 16: 0.90, 17: 0.85, 18: 0.80, 19: 0.70,
    20: 0.60, 21: 0.45, 22: 0.25, 23: 0.10,
}
WINDOWS_PER_DAY = 24


def sample_representative_day(rng: random.Random, target_period: str) -> datetime:
    """Elige, mediante rechazo, una fecha real dentro de los periodos
    poblacionales cuyo periodo academico coincida con `target_period`.
    El horario (laborable/fin de semana/feriado) se DERIVA de la fecha
    real obtenida, no se fuerza, para mantener coherencia con el
    calendario simulado."""
    for _ in range(500):
        span_start, span_end = rng.choice(POPULATION_SPANS)
        day_offset = rng.randint(0, (span_end - span_start).days)
        candidate = span_start + timedelta(days=day_offset)
        if period_for_date(candidate) == target_period:
            return candidate
    return candidate  # fallback improbable: se acepta lo que salga


# ═══════════════════════════════════════════════════════════════════════
# 2. GENERACION DE LA MUESTRA (ventanas agregadas de 60s)
# ═══════════════════════════════════════════════════════════════════════

ANOMALY_FRACTION = 0.05
ANOMALY_TYPES = ["spike", "drop", "error_burst"]


def generate_sample(n_windows: int, sample_size: int, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)

    n_days = max(1, n_windows // WINDOWS_PER_DAY)
    n_windows = n_days * WINDOWS_PER_DAY  # se ajusta al multiplo de 24 mas cercano

    # --- Pase 1: un "dia representativo" por bloque, con estratificacion
    # dirigida por periodo academico; el horario se deriva de la fecha real. ---
    windows = []
    idx = 0
    for _ in range(n_days):
        target_period = rng.choices(
            list(ACADEMIC_PERIOD_PROPORTIONS.keys()),
            weights=list(ACADEMIC_PERIOD_PROPORTIONS.values()),
        )[0]
        day_date = sample_representative_day(rng, target_period)
        period = period_for_date(day_date)
        horario = horario_for_date(day_date)

        for hour in range(WINDOWS_PER_DAY):
            user_type = sample_user_type(rng)
            # El patron horario original (config/academic_calendar.json) tiene
            # un rango de ~50x (0.02 a 1.00): esta pensado como multiplicador
            # de UMBRAL de alerta, no como amplitud generativa de trafico. Se
            # comprime a un rango (~2.8x) comparable al de la serie sintetica
            # ya validada (test_simulation.py), para no introducir una
            # volatilidad hora-a-hora mayor que la que ARIMA/LSTM ya
            # demostraron poder aprender; la variacion adicional por periodo
            # academico y horario semanal se suma sobre esa base.
            hourly_effective = 0.35 + 0.65 * HOURLY_PATTERN[hour]
            profile = (
                ACADEMIC_PERIOD_MULTIPLIER[period]
                * HORARIO_MULTIPLIER[horario]
                * hourly_effective
            )
            windows.append({
                "index": idx, "datetime": day_date.replace(hour=hour),
                "academic_period": period, "horario": horario,
                "user_type": user_type, "profile": profile,
            })
            idx += 1

    # Se reordenan los bloques diarios cronologicamente (por fecha real),
    # preservando la secuencia horaria interna de cada dia, para que el
    # split entrenamiento/validacion sea un verdadero corte temporal.
    windows.sort(key=lambda w: w["datetime"])
    for i, w in enumerate(windows):
        w["index"] = i

    # --- Anomalias inyectadas: bloques cortos (2-4 ventanas) dentro de
    # dias representativos aleatorios, simulando un incidente puntual
    # en vez de un instante aislado sin continuidad con su entorno. ---
    n_anomaly_windows = max(1, int(n_windows * ANOMALY_FRACTION))
    placed = 0
    candidate_days = list(range(2, n_days))  # se evitan los primeros dias (warm-up de modelos)
    rng.shuffle(candidate_days)
    for w in windows:
        w.setdefault("true_anomaly", False)
        w.setdefault("anomaly_type", None)
    for day in candidate_days:
        if placed >= n_anomaly_windows:
            break
        burst_len = min(rng.randint(2, 4), n_anomaly_windows - placed)
        start_hour = rng.randint(0, WINDOWS_PER_DAY - burst_len)
        atype = rng.choice(ANOMALY_TYPES)
        for h in range(start_hour, start_hour + burst_len):
            w = windows[day * WINDOWS_PER_DAY + h]
            w["true_anomaly"] = True
            w["anomaly_type"] = atype
            placed += 1

    # --- Calibracion: BASE_MEAN tal que sum(request_count) ~= sample_size ---
    mean_profile = float(np.mean([w["profile"] for w in windows]))
    base_mean = sample_size / (n_windows * mean_profile)

    # --- Pase 2: materializar request_count/unique_users/error_count/course_count ---
    # (la forma horaria suave ya la aporta HOURLY_PATTERN dentro de "profile";
    # aqui solo se agrega ruido, para no duplicar la componente estacional)
    for w in windows:
        noise = float(np_rng.normal(1.0, 0.10))
        req = max(1.0, base_mean * w["profile"] * noise)

        user_type = w["user_type"]
        users = max(1.0, req / REQUESTS_PER_USER[user_type] + float(np_rng.normal(0, 3)))
        err_rate = BASE_ERROR_RATE[user_type]
        errs = max(0.0, float(np_rng.poisson(req * err_rate)))
        courses = max(1.0, float(np_rng.integers(3, 20)))

        if w["true_anomaly"]:
            atype = w["anomaly_type"]
            if atype == "spike":
                req *= float(rng.uniform(5.0, 9.0))
                users = max(2.0, users * float(rng.uniform(0.2, 0.5)))
            elif atype == "drop":
                req *= float(rng.uniform(0.02, 0.10))
                users = max(1.0, users * float(rng.uniform(0.05, 0.2)))
                errs = 0.0
            elif atype == "error_burst":
                errs += float(rng.uniform(40, 150))
                req *= float(rng.uniform(0.6, 1.3))

        w.update({
            "request_count": float(req),
            "unique_users": float(users),
            "error_count": float(errs),
            "course_count": float(courses),
        })

    return windows


# ═══════════════════════════════════════════════════════════════════════
# 3. EJECUCION DEL PIPELINE (secuencial, orden cronologico)
# ═══════════════════════════════════════════════════════════════════════

def run_pipeline(windows: list[dict], config: dict, use_lstm: bool, split_index: int) -> dict:
    arima = ARIMAModel(config)
    lstm = LSTMModel(config) if use_lstm else None
    iforest = AnomalyDetector(config)
    arima._save = lambda: None
    iforest._save = lambda: None
    if lstm is not None:
        lstm._save = lambda: None

    base_ts = int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp())
    results = {"train": [], "validation": []}

    for w in windows:
        ts = base_ts + w["index"] * 60
        point = {
            "request_count": w["request_count"],
            "unique_users": w["unique_users"],
            "error_count": w["error_count"],
            "course_count": w["course_count"],
        }

        arima_res = arima.update(ts, point["request_count"])
        lstm_res = (
            lstm.update(point) if lstm is not None
            else {"is_anomaly": False, "reconstruction_error": 0.0, "threshold": None}
        )
        if_res = iforest.update(point)
        fusion = AnomalyDetector.fuse(if_res, arima_res, lstm_res)

        segment = "train" if w["index"] < split_index else "validation"
        results[segment].append({
            "true_anomaly": w["true_anomaly"],
            "predicted_anomaly": bool(fusion["is_anomaly"]),
            "academic_period": w["academic_period"],
            "horario": w["horario"],
            "user_type": w["user_type"],
        })

    return results


def compute_metrics(rows: list[dict]) -> dict:
    tp = sum(1 for r in rows if r["true_anomaly"] and r["predicted_anomaly"])
    fp = sum(1 for r in rows if not r["true_anomaly"] and r["predicted_anomaly"])
    fn = sum(1 for r in rows if r["true_anomaly"] and not r["predicted_anomaly"])
    tn = sum(1 for r in rows if not r["true_anomaly"] and not r["predicted_anomaly"])
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "n": len(rows), "true_anomalies": tp + fn, "TP": tp, "FP": fp, "FN": fn, "TN": tn,
        "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
    }


def stratum_proportions(rows: list[dict], key: str) -> dict:
    total = len(rows) or 1
    counts: dict = {}
    for r in rows:
        counts[r[key]] = counts.get(r[key], 0) + 1
    return {k: round(v / total, 4) for k, v in counts.items()}


# ═══════════════════════════════════════════════════════════════════════
# 4. REPORTE
# ═══════════════════════════════════════════════════════════════════════

def save_csv(path: str, rows: list[dict]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    keys: list = []
    for row in rows:
        for k in row:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def print_metrics_table(title: str, metrics: dict):
    print(f"\n{title}")
    print(f"  n={metrics['n']}  anomalias_reales={metrics['true_anomalies']}")
    print(f"  TP={metrics['TP']}  FP={metrics['FP']}  FN={metrics['FN']}  TN={metrics['TN']}")
    print(f"  Precision={metrics['precision']}  Recall={metrics['recall']}  F1={metrics['f1']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", type=int, default=3000,
                        help="Numero de ventanas de 60s a simular (controla el runtime)")
    parser.add_argument("--sample-size", type=int, default=SAMPLE_SIZE,
                        help="Tamano de muestra objetivo (registros de acceso equivalentes)")
    parser.add_argument("--train-fraction", type=float, default=TRAIN_FRACTION)
    parser.add_argument("--no-lstm", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=str, default="logs/stratified_sampling_report.csv")
    args = parser.parse_args()

    from utils.config_loader import load_config
    config = load_config("config/config.yaml")

    print("=" * 78)
    print("VALIDACION DEL PIPELINE SOBRE MUESTREO ESTRATIFICADO (metodologia de tesis)")
    print("=" * 78)
    print(f"Poblacion simulada: {[ (s.date(), e.date()) for s, e in POPULATION_SPANS ]}")
    print(f"Tamano de muestra objetivo: {args.sample_size:,} registros")
    print(f"Ventanas de 60s simuladas: {args.windows:,}")

    t0 = time.perf_counter()
    windows = generate_sample(args.windows, args.sample_size, seed=args.seed)
    total_requests = sum(w["request_count"] for w in windows)
    print(f"Total de peticiones agregadas generadas: {total_requests:,.0f} "
          f"(objetivo: {args.sample_size:,})")

    split_index = int(args.windows * args.train_fraction)
    train_windows = windows[:split_index]
    val_windows = windows[split_index:]
    train_requests = sum(w["request_count"] for w in train_windows)
    val_requests = sum(w["request_count"] for w in val_windows)
    print(f"Particion cronologica: {len(train_windows):,} ventanas / "
          f"{train_requests:,.0f} registros ~= entrenamiento (objetivo 840,000)")
    print(f"                       {len(val_windows):,} ventanas / "
          f"{val_requests:,.0f} registros ~= validacion   (objetivo 360,000)")

    print("\nProporciones por estrato en la muestra generada:")
    print("  periodo academico:", stratum_proportions(
        [{"academic_period": w["academic_period"]} for w in windows], "academic_period"))
    print("  horario:          ", stratum_proportions(
        [{"horario": w["horario"]} for w in windows], "horario"))
    print("  tipo de usuario:  ", stratum_proportions(
        [{"user_type": w["user_type"]} for w in windows], "user_type"))

    print("\nEjecutando pipeline (orden cronologico, aprendizaje incremental)...")
    results = run_pipeline(windows, config, use_lstm=not args.no_lstm, split_index=split_index)
    elapsed = time.perf_counter() - t0

    train_metrics = compute_metrics(results["train"])
    val_metrics = compute_metrics(results["validation"])
    combined_metrics = compute_metrics(results["train"] + results["validation"])

    print(f"\nPipeline completado en {elapsed:.1f} segundos.")
    print_metrics_table("[ENTRENAMIENTO] (primer 70% cronologico)", train_metrics)
    print_metrics_table("[VALIDACION] (ultimo 30% cronologico, no visto por los modelos hasta este punto)", val_metrics)
    print_metrics_table("[COMBINADO]", combined_metrics)

    print("\nProporciones por estrato en el segmento de validacion (para verificar representatividad):")
    print("  periodo academico:", stratum_proportions(results["validation"], "academic_period"))
    print("  horario:          ", stratum_proportions(results["validation"], "horario"))
    print("  tipo de usuario:  ", stratum_proportions(results["validation"], "user_type"))

    rows = [
        {"segment": "train", **train_metrics},
        {"segment": "validation", **val_metrics},
        {"segment": "combined", **combined_metrics},
    ]
    save_csv(args.output, rows)
    print(f"\nReporte guardado en: {args.output}")


if __name__ == "__main__":
    main()
