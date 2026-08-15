"""
Subsistema de modelado LSTM (RF4).
Red neuronal recurrente LSTM para capturar dependencias temporales
no lineales del trafico de la plataforma Moodle.

El modelo aprende a reconstruir la serie historica; si el error de
reconstruccion sobre un nuevo punto supera un umbral adaptativo
calculado durante el entrenamiento, el punto se clasifica como anomalia.
"""

import logging
import os
import pickle
from collections import deque
from datetime import datetime

import numpy as np

logger = logging.getLogger(__name__)

try:
    import tensorflow as tf
    from tensorflow import keras
    TF_AVAILABLE = True
except ImportError:
    TF_AVAILABLE = False
    logger.warning("TensorFlow no disponible. El modelo LSTM usara modo simulado.")


class LSTMModel:
    """
    Autoencoder LSTM para deteccion de anomalias por reconstruccion (RF4).
    """

    FEATURES = ["request_count", "unique_users", "error_count", "course_count"]

    def __init__(self, config: dict):
        lstm_cfg            = config["models"]["lstm"]
        self.seq_len        = lstm_cfg.get("sequence_length", 60)
        self.hidden_units   = lstm_cfg.get("hidden_units", 64)
        self.dropout        = lstm_cfg.get("dropout", 0.2)
        self.epochs         = lstm_cfg.get("epochs", 20)
        self.batch_size     = lstm_cfg.get("batch_size", 32)
        self.retrain_hours  = lstm_cfg.get("retrain_interval_hours", 12)

        self.model_path     = "models/saved/lstm_autoencoder.keras"
        self.scaler_path    = "models/saved/lstm_scaler.pkl"
        self.buffer_path    = "models/saved/lstm_buffer.pkl"

        self._buffer        = deque(maxlen=self.seq_len * 10)   # historial de vectores
        self._model         = None
        self._scaler        = None
        self._threshold     = None   # umbral de reconstruccion
        self._last_trained  = None
        self.MIN_SAMPLES    = self.seq_len + 10

    # ------------------------------------------------------------------
    # API publica
    # ------------------------------------------------------------------

    def update(self, point: dict) -> dict:
        """
        Incorpora un nuevo vector de features y evalua si es anomalia.

        Args:
            point: {"request_count": float, "unique_users": float, ...}

        Returns:
            {"reconstruction_error": float, "threshold": float, "is_anomaly": bool}
        """
        vec = [float(point.get(f, 0)) for f in self.FEATURES]
        self._buffer.append(vec)
        self._save_buffer()

        if len(self._buffer) < self.MIN_SAMPLES:
            return self._no_result("datos insuficientes")

        if self._needs_retrain():
            self._fit()

        if self._model is None or self._threshold is None:
            return self._no_result("modelo no entrenado")

        return self._evaluate(vec)

    def _needs_retrain(self) -> bool:
        if self._last_trained is None:
            return True
        hours = (datetime.utcnow() - self._last_trained).seconds / 3600
        return hours >= self.retrain_hours

    # ------------------------------------------------------------------
    # Entrenamiento
    # ------------------------------------------------------------------

    def _fit(self):
        if not TF_AVAILABLE:
            logger.warning("TensorFlow no instalado. LSTM omitido.")
            return
        try:
            data = np.array(list(self._buffer), dtype=np.float32)
            data, self._scaler = self._normalize(data)
            X = self._make_sequences(data)
            if len(X) < 2:
                return

            model = self._build_model(X.shape[1], X.shape[2])
            model.fit(
                X, X,
                epochs=self.epochs,
                batch_size=self.batch_size,
                validation_split=0.1,
                verbose=0,
                callbacks=[
                    keras.callbacks.EarlyStopping(patience=3, restore_best_weights=True)
                ],
            )
            self._model = model
            errors = np.mean(np.abs(model.predict(X, verbose=0) - X), axis=(1, 2))
            self._threshold = float(np.percentile(errors, 95))
            self._last_trained = datetime.utcnow()
            logger.info(
                "LSTM entrenado con %d secuencias. Umbral: %.4f", len(X), self._threshold
            )
            self._save()
        except Exception as exc:
            logger.error("Error al entrenar LSTM: %s", exc)

    def _build_model(self, seq_len: int, n_features: int) -> "keras.Model":
        inp = keras.Input(shape=(seq_len, n_features))
        # Encoder
        x = keras.layers.LSTM(self.hidden_units, activation="relu", return_sequences=False)(inp)
        x = keras.layers.Dropout(self.dropout)(x)
        x = keras.layers.RepeatVector(seq_len)(x)
        # Decoder
        x = keras.layers.LSTM(self.hidden_units, activation="relu", return_sequences=True)(x)
        x = keras.layers.Dropout(self.dropout)(x)
        out = keras.layers.TimeDistributed(keras.layers.Dense(n_features))(x)

        model = keras.Model(inp, out)
        model.compile(optimizer="adam", loss="mse")
        return model

    def _make_sequences(self, data: np.ndarray) -> np.ndarray:
        return np.array([
            data[i: i + self.seq_len]
            for i in range(len(data) - self.seq_len)
        ])

    def _normalize(self, data: np.ndarray):
        from sklearn.preprocessing import MinMaxScaler
        scaler = MinMaxScaler()
        data_scaled = scaler.fit_transform(data)
        return data_scaled, scaler

    # ------------------------------------------------------------------
    # Evaluacion
    # ------------------------------------------------------------------

    def _evaluate(self, vec: list) -> dict:
        try:
            data = np.array(list(self._buffer)[-self.seq_len:], dtype=np.float32)
            if self._scaler is not None:
                data = self._scaler.transform(data)
            X = data[np.newaxis, :, :]           # (1, seq_len, features)
            pred = self._model.predict(X, verbose=0)
            error = float(np.mean(np.abs(pred - X)))
            is_anomaly = error > self._threshold
            # Score continuo [0,1]: 0.5 exactamente en el umbral (el mismo
            # punto que antes marcaba is_anomaly), 1.0 al doble del umbral.
            # Ver nota equivalente en ARIMAModel._evaluate (RF6).
            score = min(1.0, (error / self._threshold) / 2.0) if self._threshold else 0.0
            return {
                "reconstruction_error": error,
                "threshold":            self._threshold,
                "is_anomaly":           is_anomaly,
                "score":                score,
                "model":                "LSTM",
            }
        except Exception as exc:
            logger.warning("Error al evaluar LSTM: %s", exc)
            return self._no_result(str(exc))

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _save(self):
        os.makedirs("models/saved", exist_ok=True)
        self._model.save(self.model_path)
        with open(self.scaler_path, "wb") as f:
            pickle.dump(self._scaler, f)

    def _save_buffer(self):
        """
        Persiste el buffer de vectores (no solo el modelo ya entrenado) en
        cada actualizacion, para que un reinicio del proceso retome el
        progreso acumulado hacia MIN_SAMPLES en vez de volver a cero.
        """
        os.makedirs("models/saved", exist_ok=True)
        with open(self.buffer_path, "wb") as f:
            pickle.dump(list(self._buffer), f)

    def load(self):
        if os.path.exists(self.buffer_path):
            with open(self.buffer_path, "rb") as f:
                self._buffer = deque(pickle.load(f), maxlen=self.seq_len * 10)
            logger.info("Buffer LSTM recuperado de disco: %d muestras.", len(self._buffer))
        if not TF_AVAILABLE:
            return
        if os.path.exists(self.model_path):
            self._model = keras.models.load_model(self.model_path)
        if os.path.exists(self.scaler_path):
            with open(self.scaler_path, "rb") as f:
                self._scaler = pickle.load(f)
        if self._model is not None:
            logger.info("Modelo LSTM cargado desde disco.")

    @staticmethod
    def _no_result(reason: str) -> dict:
        return {
            "reconstruction_error": 0.0,
            "threshold":            None,
            "is_anomaly":           False,
            "score":                0.0,
            "model":                "LSTM",
            "note":                 reason,
        }
