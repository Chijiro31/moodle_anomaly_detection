"""
Genera la Figura 27: comparacion del desempeno de los modelos individuales
y de las dos formulaciones del mecanismo de fusion ponderada (binaria vs.
score continuo), a partir de los resultados reales de la Tabla 13
(ONLINE_SIMULATION_REPORT.md).

Uso:
    python scripts/generate_figura_27.py
"""

import os
import matplotlib.pyplot as plt
import numpy as np

MODELOS = [
    "ARIMA",
    "LSTM\nAutoencoder",
    "Isolation\nForest",
    "Fusión\nponderada\nbinaria",
    "Fusión\nponderada por\nscore continuo",
]

# Tabla 13 (ONLINE_SIMULATION_REPORT.md), valores reales validados.
PRECISION = [0.000, 0.357, 0.667, 0.667, 0.667]
RECALL    = [0.000, 1.000, 0.400, 0.400, 0.800]
F1        = [0.000, 0.526, 0.500, 0.500, 0.727]

# Paleta categorica de orden fijo (validada colorblind-safe).
COLOR_PRECISION = "#2a78d6"  # azul
COLOR_RECALL    = "#eb6834"  # naranja
COLOR_F1        = "#1baf7a"  # aqua


def main():
    x = np.arange(len(MODELOS))
    width = 0.25

    fig, ax = plt.subplots(figsize=(11, 6))
    b1 = ax.bar(x - width, PRECISION, width, label="Precision", color=COLOR_PRECISION)
    b2 = ax.bar(x,         RECALL,    width, label="Recall",    color=COLOR_RECALL)
    b3 = ax.bar(x + width, F1,        width, label="F1",        color=COLOR_F1)

    for bars in (b1, b2, b3):
        for bar in bars:
            h = bar.get_height()
            ax.annotate(
                f"{h:.3f}" if h > 0 else "0",
                xy=(bar.get_x() + bar.get_width() / 2, h),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center", va="bottom", fontsize=8,
            )

    ax.set_ylabel("Puntuación")
    ax.set_title(
        "Comparación del desempeño de los modelos individuales y de las\n"
        "dos formulaciones del mecanismo de fusión"
    )
    ax.set_xticks(x)
    ax.set_xticklabels(MODELOS, fontsize=9)
    ax.set_ylim(0, 1.08)
    ax.legend(loc="upper left", frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle="--", alpha=0.3)

    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "figures")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "figura_27_comparacion_fusion.png")
    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    print(f"Figura 27 guardada en: {out_path}")


if __name__ == "__main__":
    main()
