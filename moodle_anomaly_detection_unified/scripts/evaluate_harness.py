"""Herramienta de evaluación para comparar predicciones con etiquetas.

Uso:
  python scripts/evaluate_harness.py --pred predictions.csv --labels labels.csv

`predictions.csv` debe contener columnas: timestamp, final_score, is_anomaly
`labels.csv` debe contener columnas: timestamp, label (0/1)

El script calcula Precision, Recall, F1 y ROC AUC si `final_score` está presente.
"""
import argparse
import pandas as pd
import numpy as np
from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score


def load_csv(path: str):
    return pd.read_csv(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pred", required=True, help="Predictions CSV")
    parser.add_argument("--labels", required=True, help="Labels CSV")
    args = parser.parse_args()

    preds = load_csv(args.pred)
    labels = load_csv(args.labels)

    # Merge on timestamp
    df = preds.merge(labels, on="timestamp", how="inner")
    if df.empty:
        print("No overlapping timestamps between predictions and labels.")
        return

    y_true = df["label"].astype(int).values
    if "is_anomaly" in df.columns:
        y_pred_bin = df["is_anomaly"].astype(int).values
    else:
        # if no binary column, derive from final_score using 0.5
        if "final_score" in df.columns:
            y_pred_bin = (df["final_score"].astype(float) >= 0.5).astype(int).values
        else:
            raise SystemExit("Predictions must contain 'is_anomaly' or 'final_score' column")

    prec = precision_score(y_true, y_pred_bin, zero_division=0)
    rec = recall_score(y_true, y_pred_bin, zero_division=0)
    f1 = f1_score(y_true, y_pred_bin, zero_division=0)

    print(f"Samples: {len(df)}")
    print(f"Precision: {prec:.4f}")
    print(f"Recall:    {rec:.4f}")
    print(f"F1:        {f1:.4f}")

    if "final_score" in df.columns:
        try:
            auc = roc_auc_score(y_true, df["final_score"].astype(float).values)
            print(f"ROC AUC:   {auc:.4f}")
        except Exception:
            print("ROC AUC:   cannot compute (constant labels or invalid scores)")


if __name__ == "__main__":
    main()
