"""
tests/generate_logs.py
======================
Genera un archivo CSV con logs sinteticos que imitan exactamente
la estructura de la tabla mdl_logstore_standard_log de Moodle.

Uso:
    python tests/generate_logs.py
    python tests/generate_logs.py --rows 2000 --out tests/data/moodle_logs.csv
"""

import argparse
import csv
import os
import random
from datetime import datetime, timedelta, timezone

# ── Valores reales extraidos de una instalacion Moodle tipica ─────────────

COMPONENTS = [
    "core", "core\\event", "mod_assign", "mod_forum", "mod_quiz",
    "mod_resource", "mod_url", "mod_page", "mod_folder",
    "core\\session", "auth_manual", "tool_log",
]

ACTIONS = [
    "viewed", "submitted", "updated", "created", "deleted",
    "loggedin", "loggedout", "failed", "graded", "downloaded",
    "attempted", "searched", "uploaded",
]

TARGETS = [
    "course", "module", "user", "question", "submission",
    "session", "grade", "file", "forum_post", "assignment",
]

CONTEXT_LEVELS = [10, 30, 40, 50, 70]   # system, coursecat, course, module, block

COURSE_IDS  = [0, 2, 5, 7, 11, 14, 18, 22, 25, 31, 45]
ERROR_ACTIONS = {"failed", "deleted"}

IP_RANGES = [
    "192.168.1.", "10.10.0.", "172.16.0.", "10.0.0.",
]


def random_ip(rng: random.Random) -> str:
    return rng.choice(IP_RANGES) + str(rng.randint(2, 254))


def generate_logs(
    n_rows: int = 1000,
    seed: int = 42,
    anomaly_windows: list = None,
) -> list[dict]:
    """
    Genera n_rows registros de log sinteticos.

    anomaly_windows: lista de tuplas (inicio_unix, fin_unix) donde
                     se inyectan picos de trafico o rafagas de errores.
    """
    rng = random.Random(seed)
    np_rng = __import__("numpy").random.default_rng(seed)

    start_dt = datetime(2026, 1, 12, 8, 0, 0, tzinfo=timezone.utc)
    # Distribuir timestamps a lo largo de 7 dias
    end_dt   = start_dt + timedelta(days=7)
    total_secs = int((end_dt - start_dt).total_seconds())

    # Patron horario de actividad (probabilidad relativa por hora)
    hourly_weights = {
        0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 1,
        6: 2, 7: 5, 8: 15, 9: 25, 10: 30, 11: 28,
        12: 20, 13: 22, 14: 28, 15: 30, 16: 27, 17: 25,
        18: 20, 19: 16, 20: 12, 21: 8,  22: 4,  23: 2,
    }

    rows = []
    user_ids = list(range(1, 301))     # 300 usuarios simulados

    # Decidir ventanas de anomalia (si no se especifican, crear 3 automaticamente)
    if anomaly_windows is None:
        base = int(start_dt.timestamp())
        anomaly_windows = [
            (base + 3600 * 10,  base + 3600 * 10 + 900,  "spike"),       # dia 1, 10am
            (base + 86400 * 2 + 3600 * 14, base + 86400 * 2 + 3600 * 14 + 600, "error_burst"),
            (base + 86400 * 4 + 3600 * 9,  base + 86400 * 4 + 3600 * 9 + 1200,  "drop"),
        ]

    for log_id in range(1, n_rows + 1):
        # Muestrear timestamp con sesgo horario
        hour_sample = rng.choices(
            list(hourly_weights.keys()),
            weights=list(hourly_weights.values()),
        )[0]
        day_offset   = rng.randint(0, 6)
        minute_offset = rng.randint(0, 59)
        sec_offset    = rng.randint(0, 59)
        ts_dt = start_dt + timedelta(
            days=day_offset, hours=hour_sample,
            minutes=minute_offset, seconds=sec_offset
        )
        ts = int(ts_dt.timestamp())

        # Determinar si cae en ventana de anomalia
        anomaly_type = None
        for (win_start, win_end, atype) in anomaly_windows:
            if win_start <= ts <= win_end:
                anomaly_type = atype
                break

        if anomaly_type == "spike":
            # Muchos usuarios distintos, muchas peticiones
            userid    = rng.randint(1, 50)
            action    = rng.choice(["viewed", "searched", "downloaded"])
            component = rng.choice(COMPONENTS)
        elif anomaly_type == "error_burst":
            # Rafaga de errores
            userid    = rng.choice([0, rng.randint(1, 300)])
            action    = rng.choice(list(ERROR_ACTIONS))
            component = rng.choice(["auth_manual", "core\\session", "core"])
        elif anomaly_type == "drop":
            # Muy pocos eventos (solo cron jobs del sistema)
            userid    = 0
            action    = "updated"
            component = "core"
        else:
            # Comportamiento normal
            userid    = rng.choice(user_ids)
            action    = rng.choices(
                ACTIONS,
                weights=[20, 10, 8, 5, 2, 15, 8, 3, 5, 6, 7, 4, 5],
            )[0]
            component = rng.choice(COMPONENTS)

        row = {
            "id":           log_id,
            "timecreated":  ts,
            "userid":       userid,
            "courseid":     rng.choice(COURSE_IDS),
            "component":    component,
            "action":       action,
            "target":       rng.choice(TARGETS),
            "objectid":     rng.randint(1, 5000),
            "contextlevel": rng.choice(CONTEXT_LEVELS),
            "ip":           random_ip(rng),
            # Columna extra solo para evaluacion de la prueba
            "_anomaly_type": anomaly_type or "",
        }
        rows.append(row)

    # Ordenar por timestamp (como lo haria MySQL ORDER BY id ASC)
    rows.sort(key=lambda r: r["timecreated"])
    # Re-asignar IDs consecutivos tras el sort
    for i, row in enumerate(rows, start=1):
        row["id"] = i

    return rows


def save_csv(rows: list[dict], path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = [
        "id", "timecreated", "userid", "courseid", "component",
        "action", "target", "objectid", "contextlevel", "ip", "_anomaly_type",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[OK] {len(rows)} logs guardados en: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--out",  type=str, default="tests/data/moodle_logs.csv")
    args = parser.parse_args()

    logs = generate_logs(n_rows=args.rows)
    save_csv(logs, args.out)

    # Resumen rapido
    anomaly_count = sum(1 for r in logs if r["_anomaly_type"])
    print(f"[INFO] Anomalias inyectadas: {anomaly_count} ({anomaly_count/len(logs)*100:.1f}%)")
    types = {}
    for r in logs:
        t = r["_anomaly_type"]
        if t:
            types[t] = types.get(t, 0) + 1
    for t, c in types.items():
        print(f"  - {t}: {c} registros")
