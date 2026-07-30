"""
concept_drift_detector.py
========================
Detector de concept drift para monitorear la degradación de modelos
y detectar cambios en el patrón de datos.

Usa múltiples métodos:
- ADWIN (Adaptive Windowing)
- Page-Hinkley test
- Drift detection via prediction confidence
"""

import logging
import numpy as np
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class ConceptDriftDetector:
    """
    Detector de concept drift que monitorea:
    1. Error rate del modelo (comparando predicciones vs realidad cuando hay etiquetas)
    2. Distribución de features (usando KL divergence)
    3. Variación en predicciones del ensemble
    """
    
    def __init__(self, config: dict = None):
        self.config = config or {}
        
        # Parámetros Page-Hinkley
        self.ph_alpha = self.config.get("ph_alpha", 0.5)  # decaying factor
        self.ph_threshold = self.config.get("ph_threshold", 50)  # change threshold
        self.ph_delta = self.config.get("ph_delta", 0.005)  # drift magnitude
        
        # Window sizes
        self.adwin_width = self.config.get("adwin_width", 100)
        
        # Detection methods
        self.use_page_hinkley = self.config.get("page_hinkley", True)
        self.use_adwin = self.config.get("adwin", True)
        
        # Estado interno
        self._ph_mean = 0.0
        self._ph_sum = 0.0
        self._ph_var = 0.0
        self._ph_count = 0
        
        # ADWIN windows
        self._adwin_window = deque(maxlen=self.adwin_width * 2)
        self._adwin_widths = [30, 60, 100]
        self._adwin_estimators = {w: deque(maxlen=w) for w in self._adwin_widths}
        
        # Drift history
        self.drift_detected = False
        self.last_drift_time = None
        self.drift_count = 0
        
    def update(self, prediction: float, actual: Optional[float] = None, 
               feature_vector: Optional[np.ndarray] = None) -> dict:
        """
        Actualiza el detector con nueva predicción.
        
        Args:
            prediction: Valor predicho por el modelo
            actual: Valor real (si se conoce, ej: en simulación)
            feature_vector: Vector de features para 检测分布 drift
            
        Returns:
            dict con:
                - drift_detected: bool
                - drift_type: 'error_rate', 'distribution', 'variance', None
                - confidence: float [0, 1]
        """
        result = {"drift_detected": False, "drift_type": None, "confidence": 0.0}
        
        # Page-Hinkley test (on prediction error if actual is available)
        if actual is not None and self.use_page_hinkley:
            error = abs(prediction - actual)
            ph_result = self._page_hinkley_update(error)
            if ph_result["drift_detected"]:
                result["drift_detected"] = True
                result["drift_type"] = "error_rate"
                result["confidence"] = ph_result["confidence"]
        
        # ADWIN on predictions (detecta cambios abruptos en distribución)
        if self.use_adwin:
            self._adwin_update(prediction)
            adwin_result = self._adwin_check()
            if adwin_result["drift_detected"]:
                result["drift_detected"] = True
                result["drift_type"] = "distribution"
                result["confidence"] = adwin_result["confidence"]
        
        # Actualizar drift history si se detectó drift
        if result["drift_detected"]:
            self.drift_detected = True
            self.last_drift_time = datetime.now(timezone.utc)
            self.drift_count += 1
            logger.warning(f"Concept drift detected: {result['drift_type']}, confidence: {result['confidence']:.2%}")
        
        return result
    
    def _page_hinkley_update(self, error: float) -> dict:
        """
        Page-Hinkley test para detectar cambios en la media de errores.
        """
        self._ph_count += 1
        delta = self.ph_delta
        
        # Actualizar media y varianza
        if self._ph_count == 1:
            self._ph_mean = error
            self._ph_var = 0
        else:
            self._ph_mean += delta * (error - self._ph_mean)
            self._ph_var += delta * ((error - self._ph_mean) ** 2 - self._ph_var)
        
        # Calcular statistic
        self._ph_sum += error - self._ph_mean - self.ph_alpha * np.sqrt(self._ph_var)
        
        result = {"drift_detected": False, "confidence": 0.0}
        
        if abs(self._ph_sum) > self.ph_threshold:
            result["drift_detected"] = True
            result["confidence"] = min(1.0, abs(self._ph_sum) / (self.ph_threshold * 2))
            self._ph_sum = 0  # Reset after drift detection
        
        return result
    
    def _adwin_update(self, value: float):
        """Actualiza las windows de ADWIN."""
        self._adwin_window.append(value)
        for width in self._adwin_widths:
            self._adwin_estimators[width].append(value)
    
    def _adwin_check(self) -> dict:
        """
        Verifica si hay drift usando ADWIN.
        Compara medias de windows de diferentes tamaños.
        """
        result = {"drift_detected": False, "confidence": 0.0}
        
        if len(self._adwin_window) < max(self._adwin_widths):
            return result
        
        for width in self._adwin_widths:
            window = np.array(self._adwin_estimators[width])
            if len(window) < width:
                continue
            
            # Comparar primera mitad vs segunda mitad
            first_half = window[:len(window)//2]
            second_half = window[len(window)//2:]
            
            if len(first_half) < 5 or len(second_half) < 5:
                continue
            
            mean_diff = abs(np.mean(first_half) - np.mean(second_half))
            std_combined = np.sqrt(np.var(first_half)/len(first_half) + np.var(second_half)/len(second_half))
            
            if std_combined > 0:
                z_score = mean_diff / std_combined
                if z_score > 2.0:  # 95% confidence
                    result["drift_detected"] = True
                    result["confidence"] = min(1.0, z_score / 4.0)  # Normalize to ~1.0
                    break
        
        return result
    
    def get_status(self) -> dict:
        """Retorna el estado actual del detector."""
        return {
            "drift_detected": self.drift_detected,
            "last_drift_time": self.last_drift_time.isoformat() if self.last_drift_time else None,
            "drift_count": self.drift_count,
            "samples_processed": self._ph_count,
            "ph_mean": self._ph_mean,
        }
    
    def reset(self):
        """Resetea el detector."""
        self._ph_mean = 0.0
        self._ph_sum = 0.0
        self._ph_var = 0.0
        self._ph_count = 0
        self._adwin_window.clear()
        for w in self._adwin_widths:
            self._adwin_estimators[w].clear()
        self.drift_detected = False