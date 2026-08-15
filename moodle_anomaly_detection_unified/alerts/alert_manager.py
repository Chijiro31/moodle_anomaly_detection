"""
Subsistema de gestion de alertas con umbrales adaptativos (RF7).
Genera notificaciones dinamicas contextualizadas con el calendario
academico de la UCI: el umbral de activacion se ajusta segun el
periodo del curso (examenes, receso, semestre normal) y los patrones
diarios y semanales conocidos.
"""

import json
import logging
import os
import smtplib
import threading
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Optional

from alerts.channels import WebhookChannel, SlackChannel

logger = logging.getLogger(__name__)


class AlertManager:
    """
    Genera alertas tempranas con umbrales adaptativos basados
    en el calendario academico (RF7).
    """

    SEVERITY_LOW    = "LOW"
    SEVERITY_MEDIUM = "MEDIUM"
    SEVERITY_HIGH   = "HIGH"

    def __init__(self, config: dict):
        self.cfg            = config
        self.alerts_cfg     = config["alerts"]
        self.cooldown_min   = self.alerts_cfg.get("cooldown_minutes", 5)
        self._lock          = threading.Lock()
        self._last_alert: Optional[datetime] = None
        self._alert_log: list = []

        # Cargar calendario academico
        cal_path = config["academic_calendar"]["file"]
        with open(cal_path, "r", encoding="utf-8") as f:
            self.calendar = json.load(f)

        # Canales adicionales de notificacion multicanal (opcionales)
        channels_cfg = self.alerts_cfg.get("channels", {})
        self._webhook = WebhookChannel(channels_cfg.get("webhook", {}))
        self._slack   = SlackChannel(channels_cfg.get("slack", {}))

        # Configurar log de alertas
        os.makedirs("logs", exist_ok=True)
        log_file = self.alerts_cfg.get("channels", {}).get("log", {}).get("file", "logs/alerts.log")
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s"))
        self._alert_logger = logging.getLogger("alerts")
        self._alert_logger.addHandler(fh)
        self._alert_logger.setLevel(logging.INFO)

    # ------------------------------------------------------------------
    # API publica
    # ------------------------------------------------------------------

    def evaluate(
        self,
        timestamp: int,
        point: dict,
        fusion_result: dict,
        arima_result: dict,
        lstm_result: dict,
    ) -> Optional[dict]:
        """
        Decide si se debe generar una alerta basandose en el resultado
        de la fusion y los umbrales adaptativos del calendario.

        Returns:
            Diccionario de alerta o None si no corresponde alertar.
        """
        if not fusion_result.get("is_anomaly", False):
            return None

        score     = fusion_result.get("final_score", 0.0)
        threshold = self._adaptive_threshold(timestamp)

        if score < threshold:
            return None

        if self._in_cooldown():
            logger.debug("Alerta suprimida por cooldown.")
            return None

        severity = self._classify_severity(score, threshold)
        alert    = self._build_alert(timestamp, point, fusion_result, arima_result, lstm_result, severity)
        self._dispatch(alert)
        return alert

    # ------------------------------------------------------------------
    # Umbral adaptativo
    # ------------------------------------------------------------------

    def _adaptive_threshold(self, ts: int) -> float:
        """
        Calcula el umbral de activacion contextualizado.
        Base: 0.50.  Se incrementa en periodos de alta actividad esperada
        (examenes) para evitar falsos positivos, y se reduce en recesos.
        """
        base = 0.50
        dt   = datetime.fromtimestamp(ts, tz=timezone.utc)

        multiplier = self._calendar_multiplier(dt)
        # En alta actividad levantamos el umbral (mas tolerante)
        # En baja actividad lo bajamos (mas sensible)
        if multiplier > 1.2:
            base = min(0.75, base + 0.10)
        elif multiplier < 0.5:
            base = max(0.30, base - 0.15)

        return round(base, 2)

    def _calendar_multiplier(self, dt: datetime) -> float:
        date_str = dt.strftime("%Y-%m-%d")
        for period in self.calendar.get("periods", []):
            if period["start"] <= date_str <= period["end"]:
                return period.get("multiplier", 1.0)
        # Patron semanal y horario como fallback
        day_name   = dt.strftime("%A").lower()
        hour_str   = str(dt.hour)
        day_factor  = self.calendar.get("weekly_pattern", {}).get(day_name, 1.0)
        hour_factor = self.calendar.get("hourly_pattern", {}).get(hour_str, 1.0)
        return day_factor * hour_factor

    # ------------------------------------------------------------------
    # Construccion y despacho de alertas
    # ------------------------------------------------------------------

    def _classify_severity(self, score: float, threshold: float) -> str:
        ratio = score / max(threshold, 0.01)
        if ratio >= 1.8:
            return self.SEVERITY_HIGH
        if ratio >= 1.3:
            return self.SEVERITY_MEDIUM
        return self.SEVERITY_LOW

    def _build_alert(
        self, ts, point, fusion, arima, lstm, severity
    ) -> dict:
        return {
            "timestamp":    ts,
            "datetime":     datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "severity":     severity,
            "final_score":  fusion.get("final_score"),
            "votes":        fusion.get("votes", {}),
            "metrics":      point,
            "arima": {
                "predicted":  arima.get("predicted"),
                "lower":      arima.get("lower"),
                "upper":      arima.get("upper"),
            },
            "lstm": {
                "reconstruction_error": lstm.get("reconstruction_error"),
                "threshold":            lstm.get("threshold"),
            },
        }

    def _dispatch(self, alert: dict):
        with self._lock:
            self._last_alert = datetime.utcnow()
            self._alert_log.append(alert)

        msg = (
            f"[{alert['severity']}] Anomalia detectada | "
            f"Score={alert['final_score']:.3f} | "
            f"Votos={alert['votes']} | "
            f"Metricas={alert['metrics']}"
        )
        logger.warning(msg)
        self._alert_logger.info(msg)

        if self.alerts_cfg.get("channels", {}).get("email", {}).get("enabled", False):
            self._send_email(alert)
        self._webhook.send(alert)
        self._slack.send(alert)

    def _in_cooldown(self) -> bool:
        if self._last_alert is None:
            return False
        elapsed = (datetime.utcnow() - self._last_alert).seconds / 60
        return elapsed < self.cooldown_min

    # ------------------------------------------------------------------
    # Email (opcional)
    # ------------------------------------------------------------------

    def _send_email(self, alert: dict):
        try:
            email_cfg = self.alerts_cfg["channels"]["email"]
            body      = json.dumps(alert, indent=2, ensure_ascii=False)
            msg       = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = f"[Moodle Monitor] Anomalia {alert['severity']}"
            msg["From"]    = email_cfg["sender"]
            msg["To"]      = ", ".join(email_cfg["recipients"])

            with smtplib.SMTP(email_cfg["smtp_host"], email_cfg["smtp_port"]) as server:
                server.starttls()
                server.sendmail(email_cfg["sender"], email_cfg["recipients"], msg.as_string())
            logger.info("Alerta enviada por correo.")
        except Exception as exc:
            logger.error("Error al enviar correo: %s", exc)

    # ------------------------------------------------------------------
    # Historial
    # ------------------------------------------------------------------

    def get_recent_alerts(self, n: int = 50) -> list:
        return list(self._alert_log[-n:])
