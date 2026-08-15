"""
Genera la Figura 25: evolucion del score de anomalia generado por los
modelos analiticos durante la simulacion en linea, a partir de los datos
reales almacenados en InfluxDB (no una captura de pantalla de Grafana,
sino el mismo dato subyacente, graficado con matplotlib para mayor
resolucion y consistencia visual con el resto de figuras del documento).

Uso:
    python scripts/generate_figura_25.py
"""

import csv
import os
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import matplotlib.dates as mdates

SCORES_CSV = "/tmp/figura25_scores.csv"
TRAFFIC_CSV = "/tmp/figura25_traffic.csv"

ANOMALIAS_REALES = {
    "2026-08-04T19:49:00Z": "Fuerza bruta + pico",
    "2026-08-05T02:11:00Z": "Ráfaga de errores",
    "2026-08-05T02:13:00Z": "Manipulación de URL",
    "2026-08-05T02:14:00Z": "Caída de tráfico (1)",
    "2026-08-05T02:15:00Z": "Caída de tráfico (2)",
}

COLOR_ARIMA  = "#2a78d6"
COLOR_LSTM   = "#eb6834"
COLOR_IF     = "#1baf7a"
COLOR_FUSION = "#4a3aa7"
COLOR_ANOM   = "#e34948"


def load(path):
    rows = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = None
        for row in reader:
            if not row:
                continue
            if row[1] == "result":
                header = row
                continue
            if header and row[1] == "_result":
                rows.append(dict(zip(header, row)))
    return rows


def arima_score(d, actual):
    try:
        predicted = float(d["arima_predicted"])
        lower = float(d["arima_lower"])
        upper = float(d["arima_upper"])
    except Exception:
        return 0.0
    half_width = max((upper - lower) / 2.0, 1e-6)
    return min(1.0, (abs(actual - predicted) / half_width) / 2.0)


def lstm_score(d):
    try:
        error = float(d["lstm_recon_error"])
        threshold = float(d["lstm_threshold"])
        if threshold <= 0:
            return 0.0
    except Exception:
        return 0.0
    return min(1.0, (error / threshold) / 2.0)


def if_score(d):
    try:
        return float(d["if_score"])
    except Exception:
        return 0.0


def main():
    scores = load(SCORES_CSV)
    traffic = load(TRAFFIC_CSV)
    traffic_by_ts = {d["_time"]: float(d["_value"]) for d in traffic}

    times, s_arima, s_lstm, s_if, s_fusion = [], [], [], [], []
    for d in scores:
        ts = d["_time"]
        actual = traffic_by_ts.get(ts, 0.0)
        t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        times.append(t)
        s_arima.append(arima_score(d, actual))
        s_lstm.append(lstm_score(d))
        s_if.append(if_score(d))
        s_fusion.append(float(d.get("final_score", 0.0)))

    fig, ax = plt.subplots(figsize=(13, 6))
    ax.plot(times, s_arima, label="ARIMA (score)", color=COLOR_ARIMA, linewidth=1.3, alpha=0.85)
    ax.plot(times, s_lstm, label="LSTM Autoencoder (score)", color=COLOR_LSTM, linewidth=1.3, alpha=0.85)
    ax.plot(times, s_if, label="Isolation Forest (score)", color=COLOR_IF, linewidth=1.3, alpha=0.85)
    ax.plot(times, s_fusion, label="Fusión ponderada binaria (final_score)", color=COLOR_FUSION, linewidth=1.8)

    ax.axhline(0.5, color="gray", linestyle=":", linewidth=1, label="Umbral binario (0.5)")

    for ts, etiqueta in ANOMALIAS_REALES.items():
        t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        ax.axvline(t, color=COLOR_ANOM, linestyle="--", linewidth=1, alpha=0.6)

    ax.set_ylabel("Score de anomalía [0, 1]")
    ax.set_title(
        "Evolución del score de anomalía generado por los modelos analíticos\n"
        "durante la simulación en línea"
    )
    ax.set_ylim(-0.02, 1.05)
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b %H:%M", tz=timezone.utc))
    fig.autofmt_xdate()

    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "figures")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "figura_25_evolucion_score.png")
    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    print(f"Figura 25 guardada en: {out_path} ({len(times)} ventanas graficadas)")


if __name__ == "__main__":
    main()
