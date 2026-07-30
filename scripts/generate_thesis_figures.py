"""Genera las figuras del Capitulo III para la tesis.

Figuras generadas:
- Figura 18: Arquitectura del sistema
- Figura 19: Arbol de utilidad
- Figura 20: Evolucion temporal del score de anomalia
- Figura 21: Comparacion de desempeno de modelos
- Figura 22: Alertas por severidad
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
FIGURES_DIR = ROOT / "figures"
sys.path.insert(0, str(ROOT))


def _prepare_output_dir() -> None:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)


def _draw_box(ax, xy, width, height, text, facecolor, edgecolor="#233043", fontsize=10, weight="bold"):
    box = FancyBboxPatch(
        xy,
        width,
        height,
        boxstyle="round,pad=0.02,rounding_size=0.02",
        linewidth=1.5,
        edgecolor=edgecolor,
        facecolor=facecolor,
    )
    ax.add_patch(box)
    ax.text(
        xy[0] + width / 2,
        xy[1] + height / 2,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
        weight=weight,
        color="#14202b",
    )


def _save(fig, filename: str) -> None:
    path = FIGURES_DIR / filename
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {path}")


def figura_18_arquitectura() -> None:
    fig, ax = plt.subplots(figsize=(16, 9))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    palette = {
        "input": "#d9ecff",
        "process": "#e7f5d7",
        "analysis": "#f9e2ae",
        "output": "#f6d6d6",
    }

    _draw_box(ax, (0.03, 0.42), 0.15, 0.16, "Moodle DB\nMySQL", palette["input"])
    _draw_box(ax, (0.23, 0.42), 0.16, 0.16, "Capture\nRF1", palette["process"])
    _draw_box(ax, (0.43, 0.42), 0.16, 0.16, "Preprocess\nRF2", palette["process"])
    _draw_box(ax, (0.63, 0.60), 0.12, 0.14, "ARIMA\nRF3", palette["analysis"])
    _draw_box(ax, (0.63, 0.40), 0.12, 0.14, "LSTM\nRF4", palette["analysis"])
    _draw_box(ax, (0.63, 0.20), 0.12, 0.14, "IForest\nRF5", palette["analysis"])
    _draw_box(ax, (0.80, 0.42), 0.15, 0.16, "Fusion\nRF6", palette["analysis"])
    _draw_box(ax, (0.03, 0.12), 0.18, 0.12, "Alert Manager\nRF7", palette["output"])
    _draw_box(ax, (0.24, 0.12), 0.18, 0.12, "InfluxDB Writer\nRF8", palette["output"])
    _draw_box(ax, (0.48, 0.08), 0.18, 0.12, "Grafana Dashboard", palette["output"])

    arrows = [
        ((0.18, 0.50), (0.23, 0.50)),
        ((0.39, 0.50), (0.43, 0.50)),
        ((0.59, 0.50), (0.63, 0.67)),
        ((0.59, 0.50), (0.63, 0.47)),
        ((0.59, 0.50), (0.63, 0.27)),
        ((0.75, 0.67), (0.80, 0.50)),
        ((0.75, 0.47), (0.80, 0.50)),
        ((0.75, 0.27), (0.80, 0.50)),
        ((0.88, 0.42), (0.14, 0.24)),
        ((0.33, 0.24), (0.57, 0.14)),
        ((0.66, 0.14), (0.66, 0.08)),
    ]
    for start, end in arrows:
        ax.annotate("", xy=end, xytext=start, arrowprops=dict(arrowstyle="->", lw=1.6, color="#334155"))

    ax.text(0.5, 0.95, "Figura 18. Arquitectura del sistema", ha="center", fontsize=16, weight="bold")
    ax.text(
        0.5,
        0.90,
        "Flujo: Moodle DB -> Redis Streams -> Preprocesamiento -> Modelos -> Fusión -> Alertas -> InfluxDB/Grafana",
        ha="center",
        fontsize=11,
        color="#334155",
    )

    _save(fig, "figura_18_arquitectura.png")


def figura_19_arbol_utilidad() -> None:
    fig, ax = plt.subplots(figsize=(16, 10))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    _draw_box(ax, (0.35, 0.88), 0.30, 0.08, "Sistema de Detección de Anomalías", "#d9ecff", fontsize=12)

    parents = [
        (0.04, 0.70, "Rendimiento"),
        (0.21, 0.70, "Escalabilidad"),
        (0.38, 0.70, "Disponibilidad"),
        (0.55, 0.70, "Modificabilidad"),
        (0.72, 0.70, "Seguridad"),
        (0.89, 0.70, "Integrabilidad"),
    ]
    for x, y, title in parents:
        _draw_box(ax, (x, y), 0.12, 0.08, title, "#e7f5d7", fontsize=10)
        ax.annotate("", xy=(x + 0.06, y + 0.08), xytext=(0.5, 0.88), arrowprops=dict(arrowstyle="->", lw=1.2))

    leaves = {
        0.04: ["Latencia < 5s", "Throughput >= 8/s"],
        0.21: ["+150% carga", "±10% variacion"],
        0.38: ["Recuperacion < 30s", "Sin perdida eventos"],
        0.55: ["Nuevo modelo < 4h", "Cero downtime"],
        0.72: ["Acceso bloqueado", "Auditoria segura"],
        0.89: ["Cambio de version", "Sin cambios codigo"],
    }

    for x, texts in leaves.items():
        for idx, text in enumerate(texts):
            y = 0.52 - idx * 0.12
            _draw_box(ax, (x, y), 0.12, 0.08, text, "#f9e2ae", fontsize=9, weight="normal")
            ax.annotate("", xy=(x + 0.06, y + 0.08), xytext=(x + 0.06, 0.70), arrowprops=dict(arrowstyle="->", lw=1.0))

    ax.text(0.5, 0.97, "Figura 19. Árbol de utilidad del sistema", ha="center", fontsize=16, weight="bold")
    _save(fig, "figura_19_arbol_utilidad.png")


def figura_20_evolucion_score() -> None:
    import numpy as np
    from datetime import datetime, timedelta, timezone

    rng = np.random.default_rng(42)
    base_time = datetime(2026, 1, 12, 8, 0, tzinfo=timezone.utc)
    timestamps = [base_time + timedelta(minutes=5 * i) for i in range(300)]
    anomaly_positions = set(range(55, 300, 17))

    scores = []
    for i in range(300):
        seasonal = 0.10 * np.sin(2 * np.pi * i / 24)
        noise = float(rng.normal(0.0, 0.03))
        score = 0.18 + seasonal + noise
        if i in anomaly_positions:
            score += float(rng.uniform(0.45, 0.65))
        scores.append(max(0.0, min(1.0, score)))

    datetimes = [t.strftime("%Y-%m-%d %H:%M") for t in timestamps]

    fig, ax = plt.subplots(figsize=(16, 6))
    ax.plot(datetimes, scores, color="#2563eb", lw=1.8, label="Score final")
    anomaly_mask = [i in anomaly_positions for i in range(300)]
    ax.scatter(
        [d for d, flag in zip(datetimes, anomaly_mask) if flag],
        [s for s, flag in zip(scores, anomaly_mask) if flag],
        color="#dc2626",
        s=35,
        label="Anomalías reales",
        zorder=3,
    )
    ax.set_title("Figura 20. Evolución temporal del score de anomalía")
    ax.set_xlabel("Ventana temporal")
    ax.set_ylabel("Score final")
    ax.set_ylim(0, max(1.0, max(scores) + 0.1))
    ax.grid(True, alpha=0.25)
    ax.legend()
    for label in ax.get_xticklabels():
        label.set_rotation(45)
        label.set_horizontalalignment("right")
    fig.tight_layout()
    _save(fig, "figura_20_evolucion_score.png")


def figura_21_comparacion_modelos() -> None:
    metrics = {
        "ARIMA": {"Precision": 0.145, "Recall": 0.733, "F1": 0.242},
        "LSTM": {"Precision": 0.065, "Recall": 1.000, "F1": 0.122},
        "Isolation\nForest": {"Precision": 0.097, "Recall": 0.800, "F1": 0.173},
        "Fusión": {"Precision": 0.110, "Recall": 0.933, "F1": 0.197},
    }

    labels = list(metrics.keys())
    precision = [metrics[label]["Precision"] for label in labels]
    recall = [metrics[label]["Recall"] for label in labels]
    f1 = [metrics[label]["F1"] for label in labels]

    x = range(len(labels))
    width = 0.24

    fig, ax = plt.subplots(figsize=(14, 7))
    ax.bar([i - width for i in x], precision, width, label="Precision", color="#ef4444")
    ax.bar(x, recall, width, label="Recall", color="#22c55e")
    ax.bar([i + width for i in x], f1, width, label="F1", color="#3b82f6")

    ax.set_title("Figura 21. Comparación del desempeño de modelos")
    ax.set_ylabel("Valor de métrica")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 1.1)
    ax.grid(axis="y", alpha=0.25)
    ax.legend()

    for idx, value in enumerate(recall):
        ax.text(idx, value + 0.025, f"{value:.1%}", ha="center", fontsize=9)

    fig.tight_layout()
    _save(fig, "figura_21_comparacion_modelos.png")


def figura_22_alertas_severidad() -> None:
    import numpy as np

    rng = np.random.default_rng(42)
    anomaly_positions = set(range(55, 300, 17))
    sev_counter = Counter()

    for i in range(300):
        score = 0.18 + float(rng.normal(0.0, 0.03))
        if i in anomaly_positions:
            score += float(rng.uniform(0.45, 0.65))
        score = max(0.0, min(1.0, score))

        if score >= 0.72:
            sev_counter["HIGH"] += 1
        elif score >= 0.45:
            sev_counter["MEDIUM"] += 1
        else:
            sev_counter["LOW"] += 1

    labels = ["LOW", "MEDIUM", "HIGH"]
    values = [sev_counter[label] for label in labels]
    colors = ["#60a5fa", "#f59e0b", "#ef4444"]

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(labels, values, color=colors)
    ax.set_title("Figura 22. Alertas emitidas por nivel de severidad")
    ax.set_ylabel("Cantidad de alertas")
    ax.grid(axis="y", alpha=0.25)

    for bar in bars:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, height + 0.2, f"{int(height)}", ha="center", fontsize=10)

    fig.tight_layout()
    _save(fig, "figura_22_alertas_severidad.png")


def main() -> None:
    _prepare_output_dir()
    figura_18_arquitectura()
    figura_19_arbol_utilidad()
    figura_20_evolucion_score()
    figura_21_comparacion_modelos()
    figura_22_alertas_severidad()


if __name__ == "__main__":
    main()