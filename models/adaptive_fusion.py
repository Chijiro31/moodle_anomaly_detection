"""
Fusion adaptativa de modelos (extension de RF6).
Variante opcional de AnomalyDetector.fuse() que ajusta los pesos de
cada modelo en funcion de su desempeno historico, en lugar de usar
pesos fijos. Se activa mediante config["adaptive_fusion"]["enabled"].

Los pesos iniciales coinciden por defecto con los validados en la
simulacion offline (ver models/anomaly_detector.py::MODEL_WEIGHTS), por
lo que mientras no se invoque update() con retroalimentacion real, su
comportamiento es identico al de la fusion estatica.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class AdaptiveFusion:
    """
    Fusion ponderada de modelos con pesos ajustables (RF6, aprendizaje
    continuo). Los pesos se recalculan a partir de la tasa de acierto
    reciente de cada modelo cuando se dispone de retroalimentacion
    (por ejemplo, confirmacion manual de una alerta).
    """

    def __init__(self, config: dict = None):
        cfg = config or {}
        initial = cfg.get("initial_weights", {})
        self.weights = {
            "isolation_forest": initial.get("isolation_forest", 1.0),
            "arima":            initial.get("arima", 0.8),
            "lstm":             initial.get("lstm", 1.2),
        }
        self.min_weight = cfg.get("min_weight", 0.1)
        self.max_weight = cfg.get("max_weight", 2.0)
        self._performance_window = cfg.get("performance_window", 100)
        self._recent_results = []

    def update(self, model_name: str, was_correct: bool):
        """Actualiza el peso de un modelo segun su rendimiento reciente."""
        self._recent_results.append({
            "model": model_name,
            "correct": was_correct,
            "timestamp": datetime.now(timezone.utc).timestamp(),
        })
        if len(self._recent_results) > self._performance_window:
            self._recent_results.pop(0)
        self._adjust_weight(model_name)

    def _adjust_weight(self, model_name: str):
        model_results = [r for r in self._recent_results if r["model"] == model_name]
        if len(model_results) < 10:
            return

        correct_rate = sum(1 for r in model_results if r["correct"]) / len(model_results)
        current_weight = self.weights.get(model_name, 1.0)
        new_weight = current_weight

        if correct_rate > 0.7:
            new_weight = min(current_weight * 1.1, self.max_weight)
        elif correct_rate < 0.4:
            new_weight = max(current_weight * 0.9, self.min_weight)

        self.weights[model_name] = new_weight
        logger.info(
            "Peso de %s ajustado: %.2f -> %.2f (acierto: %.2f%%)",
            model_name, current_weight, new_weight, correct_rate * 100,
        )

    def get_weights(self) -> dict:
        return self.weights.copy()

    def fuse(self, if_result: dict, arima_result: dict, lstm_result: dict) -> dict:
        """Fusion ponderada con los pesos adaptativos actuales."""
        votes = {
            "isolation_forest": 1.0 if if_result.get("is_anomaly", False) else 0.0,
            "arima":            1.0 if arima_result.get("is_anomaly", False) else 0.0,
            "lstm":             1.0 if lstm_result.get("is_anomaly", False) else 0.0,
        }
        total_weight = sum(self.weights.values())
        weighted_score = sum(votes[m] * self.weights[m] for m in votes) / total_weight
        is_anomaly = weighted_score >= 0.5

        return {
            "final_score":  round(weighted_score, 4),
            "is_anomaly":   is_anomaly,
            "votes":        votes,
            "models_used":  list(votes.keys()),
            "weights_used": self.weights.copy(),
        }
