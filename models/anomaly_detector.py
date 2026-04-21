"""
Subsistema de deteccion de anomalias no supervisada (RF5).
Utiliza Isolation Forest para identificar comportamientos anomalos
sin necesidad de datos etiquetados, respondiendo a la ausencia de
datasets anotados en el contexto real de aplicacion.

Ademas implementa el mecanismo de fusion de resultados (RF6):
combina las senales de ARIMA, LSTM e Isolation Forest mediante
votacion ponderada para producir una decision final.
"""

import logging
import os
import pickle
from collections import deque
from datetime import datetime

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

# Pesos para la fusion de resultados (RF6)
MODEL_WEIGHTS = {
    "isolation_forest": 1.0,
    "arima":            0.8,
    "lstm":             1.2,
}


class AnomalyDetector:
    """
    Detector no supervisado basado en Isolation Forest (RF5).
    Incluye el mecanismo de fusion de las tres senales (RF6).
    """

    FEATURES = ["request_count", "unique_users", "error_count", "course_count"]

    def __init__(self, config: dict):
        iforest_cfg        = config["models"]["anomaly_detector"]
        self.contamination = iforest_cfg.get("contamination", 0.05)
        self.n_estimators  = iforest_cfg.get("n_estimators", 100)
        self.model_path    = "models/saved/isolation_forest.pkl"
        self.scaler_path   = "models/saved/if_scaler.pkl"

        self._buffer       = deque(maxlen=5000)
        self._model        = None
        self._scaler       = None
        self._last_trained = None
        self.RETRAIN_EVERY = 500          # muestras entre reentrenamientos
        self._sample_count = 0
        self.MIN_SAMPLES   = 100

    # ------------------------------------------------------------------
    # API publica
    # ------------------------------------------------------------------

    def update(self, point: dict) -> dict:
        """
        Evalua un nuevo punto con Isolation Forest.

        Returns:
            {"score": float, "is_anomaly": bool, "model": "IsolationForest"}
        """
        vec = [float(point.get(f, 0)) for f in self.FEATURES]
        self._buffer.append(vec)
        self._sample_count += 1

        if len(self._buffer) < self.MIN_SAMPLES:
            return self._no_result("datos insuficientes")

        if self._needs_retrain():
            self._fit()

        if self._model is None:
            return self._no_result("modelo no entrenado")

        return self._evaluate(vec)

    @staticmethod
    def fuse(if_result: dict, arima_result: dict, lstm_result: dict) -> dict:
        """
        Fusion ponderada de los tres modelos (RF6).

        Calcula un score de anomalia entre 0 y 1 basado en los votos
        ponderados de cada modelo.

        Returns:
            {
                "final_score": float [0, 1],
                "is_anomaly":  bool,
                "votes":       dict,
            }
        """
        votes = {
            "isolation_forest": 1.0 if if_result.get("is_anomaly", False) else 0.0,
            "arima":            1.0 if arima_result.get("is_anomaly", False) else 0.0,
            "lstm":             1.0 if lstm_result.get("is_anomaly", False) else 0.0,
        }
        total_weight = sum(MODEL_WEIGHTS.values())
        weighted_score = sum(votes[m] * MODEL_WEIGHTS[m] for m in votes) / total_weight
        is_anomaly = weighted_score >= 0.5

        return {
            "final_score": round(weighted_score, 4),
            "is_anomaly":  is_anomaly,
            "votes":       votes,
            "models_used": list(votes.keys()),
        }

    # ------------------------------------------------------------------
    # Entrenamiento interno
    # ------------------------------------------------------------------

    def _needs_retrain(self) -> bool:
        if self._model is None:
            return True
        return self._sample_count % self.RETRAIN_EVERY == 0

    def _fit(self):
        data = np.array(list(self._buffer), dtype=np.float32)
        scaler = StandardScaler()
        data_scaled = scaler.fit_transform(data)

        model = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(data_scaled)
        self._model  = model
        self._scaler = scaler
        self._last_trained = datetime.utcnow()
        logger.info("Isolation Forest reentrenado con %d muestras.", len(data))
        self._save()

    def _evaluate(self, vec: list) -> dict:
        X = np.array([vec], dtype=np.float32)
        X_scaled = self._scaler.transform(X)
        score = float(self._model.score_samples(X_scaled)[0])
        # score_samples devuelve valores negativos; mas negativo = mas anomalo
        # Normalizamos a [0, 1] donde 1 = mas anomalo
        normalized = max(0.0, min(1.0, (-score - 0.3) / 0.3))
        is_anomaly = self._model.predict(X_scaled)[0] == -1
        return {
            "score":      normalized,
            "raw_score":  score,
            "is_anomaly": is_anomaly,
            "model":      "IsolationForest",
        }

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _save(self):
        os.makedirs("models/saved", exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump(self._model, f)
        with open(self.scaler_path, "wb") as f:
            pickle.dump(self._scaler, f)

    def load(self):
        if os.path.exists(self.model_path) and os.path.exists(self.scaler_path):
            with open(self.model_path, "rb") as f:
                self._model = pickle.load(f)
            with open(self.scaler_path, "rb") as f:
                self._scaler = pickle.load(f)
            logger.info("Isolation Forest cargado desde disco.")

    @staticmethod
    def _no_result(reason: str) -> dict:
        return {
            "score":      0.0,
            "is_anomaly": False,
            "model":      "IsolationForest",
            "note":       reason,
        }
