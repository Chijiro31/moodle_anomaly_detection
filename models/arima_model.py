"""
Subsistema de modelado estadistico ARIMA (RF3).
Ajusta un modelo SARIMA sobre la serie de 'request_count' para
capturar patrones lineales y estacionales.  Devuelve la prediccion
del siguiente periodo y el intervalo de confianza; si el valor real
cae fuera del intervalo se marca como posible anomalia.
"""

import logging
import pickle
import os
from datetime import datetime

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tools.sm_exceptions import ConvergenceWarning
import warnings

warnings.filterwarnings("ignore", category=ConvergenceWarning)

logger = logging.getLogger(__name__)


class ARIMAModel:
    """
    Modelo SARIMA que aprende el comportamiento normal del trafico
    y detecta desviaciones estadisticas significativas (RF3).
    """

    def __init__(self, config: dict):
        arima_cfg = config["models"]["arima"]
        self.order          = tuple(arima_cfg["order"])
        self.seasonal_order = tuple(arima_cfg["seasonal_order"])
        self.retrain_hours  = arima_cfg.get("retrain_interval_hours", 6)
        self.model_path     = "models/saved/arima_model.pkl"
        self._model_fit     = None
        self._last_trained  = None
        self._history: list = []           # buffer de valores historicos
        self.MIN_SAMPLES    = 50           # minimo para entrenar

    # ------------------------------------------------------------------
    # API publica
    # ------------------------------------------------------------------

    def update(self, timestamp: int, value: float) -> dict:
        """
        Agrega un nuevo punto y devuelve el resultado de la evaluacion.

        Returns:
            {
                "predicted": float,
                "lower": float,
                "upper": float,
                "is_anomaly": bool,
                "confidence_interval": 0.95,
            }
        """
        self._history.append((timestamp, value))

        if len(self._history) < self.MIN_SAMPLES:
            return self._no_prediction(value, reason="datos insuficientes")

        if self._needs_retrain():
            self._fit()

        if self._model_fit is None:
            return self._no_prediction(value, reason="modelo no entrenado")

        return self._evaluate(value)

    def _needs_retrain(self) -> bool:
        if self._last_trained is None:
            return True
        hours_elapsed = (datetime.utcnow() - self._last_trained).seconds / 3600
        return hours_elapsed >= self.retrain_hours

    # ------------------------------------------------------------------
    # Entrenamiento
    # ------------------------------------------------------------------

    def _fit(self):
        series = pd.Series([v for _, v in self._history])
        try:
            model = SARIMAX(
                series,
                order=self.order,
                seasonal_order=self.seasonal_order,
                enforce_stationarity=False,
                enforce_invertibility=False,
            )
            self._model_fit = model.fit(disp=False)
            self._last_trained = datetime.utcnow()
            logger.info(
                "ARIMA(%s)x(%s) entrenado con %d muestras.",
                self.order, self.seasonal_order, len(series)
            )
            self._save()
        except Exception as exc:
            logger.warning("Error al entrenar ARIMA: %s", exc)

    def _save(self):
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump(self._model_fit, f)

    def load(self):
        if os.path.exists(self.model_path):
            with open(self.model_path, "rb") as f:
                self._model_fit = pickle.load(f)
            logger.info("Modelo ARIMA cargado desde disco.")

    # ------------------------------------------------------------------
    # Evaluacion
    # ------------------------------------------------------------------

    def _evaluate(self, actual: float) -> dict:
        try:
            forecast = self._model_fit.get_forecast(steps=1)
            predicted = float(forecast.predicted_mean.iloc[0])
            ci        = forecast.conf_int(alpha=0.05)
            lower     = float(ci.iloc[0, 0])
            upper     = float(ci.iloc[0, 1])
            is_anomaly = actual < lower or actual > upper
            return {
                "predicted":            predicted,
                "lower":                lower,
                "upper":                upper,
                "is_anomaly":           is_anomaly,
                "confidence_interval":  0.95,
                "model":                "ARIMA",
            }
        except Exception as exc:
            logger.warning("Error al evaluar ARIMA: %s", exc)
            return self._no_prediction(actual, reason=str(exc))

    @staticmethod
    def _no_prediction(value: float, reason: str = "") -> dict:
        return {
            "predicted":           value,
            "lower":               0.0,
            "upper":               float("inf"),
            "is_anomaly":          False,
            "confidence_interval": 0.95,
            "model":               "ARIMA",
            "note":                reason,
        }
