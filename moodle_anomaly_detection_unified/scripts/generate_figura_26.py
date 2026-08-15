"""
Genera la Figura 26: complementariedad de los modelos en el espacio
Precision-Recall. Isolation Forest y el LSTM Autoencoder ocupan posiciones
opuestas (alta precision/baja cobertura vs. alta cobertura/baja precision);
la fusion por score continuo combina ambas fortalezas y se ubica en una
posicion superior a las dos formulaciones de fusion y a los 3 modelos
individuales. Datos: Tabla 12 (ONLINE_SIMULATION_REPORT.md).

Uso:
    python scripts/generate_figura_26.py
"""

import os
import numpy as np
import matplotlib.pyplot as plt

# (nombre, recall, precision, color, offset de etiqueta (dx, dy))
PUNTOS = [
    ("ARIMA",                                0.000, 0.000, "#2a78d6", (0.02, 0.03)),
    ("LSTM Autoencoder",                     1.000, 0.357, "#eb6834", (-0.02, 0.04)),
    ("Isolation Forest /\nFusión binaria",   0.400, 0.667, "#1baf7a", (0.02, -0.06)),
    ("Fusión por\nscore continuo",           0.800, 0.667, "#e87ba4", (0.02, 0.04)),
]


def f1_isoline(f1, recall_range):
    """Precision necesaria para un F1 dado, en funcion del recall."""
    with np.errstate(divide="ignore", invalid="ignore"):
        precision = (f1 * recall_range) / (2 * recall_range - f1)
    precision = np.where((precision < 0) | (precision > 1), np.nan, precision)
    return precision


def main():
    fig, ax = plt.subplots(figsize=(8, 7))

    # Isolineas de F1 constante, como referencia visual de fondo.
    r = np.linspace(0.01, 1, 300)
    for f1_val in [0.2, 0.4, 0.6, 0.8]:
        p = f1_isoline(f1_val, r)
        ax.plot(r, p, color="lightgray", linewidth=0.8, linestyle="--", zorder=1)
        valid = ~np.isnan(p)
        if valid.any():
            idx = np.where(valid)[0][-1]
            ax.annotate(f"F1={f1_val}", (r[idx], p[idx]), fontsize=7,
                        color="gray", ha="left", va="center")

    for nombre, recall, precision, color, (dx, dy) in PUNTOS:
        ax.scatter([recall], [precision], s=140, color=color, zorder=3,
                   edgecolors="white", linewidths=1.2)
        ax.annotate(nombre, (recall, precision), xytext=(recall + dx, precision + dy),
                    fontsize=9.5, ha="center", zorder=4)

    ax.set_xlabel("Recall")
    ax.set_ylabel("Precision")
    ax.set_title(
        "Complementariedad de los modelos en el espacio Precision-Recall"
    )
    ax.set_xlim(-0.03, 1.05)
    ax.set_ylim(-0.03, 1.05)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(alpha=0.25)

    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "figures")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "figura_26_complementariedad.png")
    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    print(f"Figura 26 guardada en: {out_path}")


if __name__ == "__main__":
    main()
