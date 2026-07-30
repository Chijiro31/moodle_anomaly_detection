"""
Canales de notificacion adicionales para el subsistema de alertas (RF7).
El canal de correo y el log de alertas se gestionan directamente en
alert_manager.py; aqui se definen los canales HTTP genericos (Webhook y
Slack) para notificacion multicanal.
"""

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)


class WebhookChannel:
    """Canal de alertas via HTTP Webhook generico."""

    def __init__(self, config: dict):
        self.url     = config.get("url", "")
        self.enabled = config.get("enabled", False)
        self.headers = config.get("headers", {"Content-Type": "application/json"})
        self.method  = config.get("method", "POST")

    def send(self, alert: dict) -> bool:
        if not self.enabled or not self.url:
            return True

        payload = {
            "event":       "moodle_anomaly",
            "timestamp":   alert.get("timestamp"),
            "datetime":    alert.get("datetime"),
            "severity":    alert.get("severity"),
            "final_score": alert.get("final_score", 0),
            "metrics":     alert.get("metrics", {}),
            "votes":       alert.get("votes", {}),
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.url, data=data, headers=self.headers, method=self.method
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info("Alerta enviada por webhook, respuesta: %s", resp.status)
                return resp.status < 400
        except Exception as exc:
            logger.error("Error al enviar alerta por webhook: %s", exc)
            return False


class SlackChannel:
    """Canal de alertas para Slack via Incoming Webhook."""

    def __init__(self, config: dict):
        self.webhook_url = config.get("webhook_url", "")
        self.enabled     = config.get("enabled", False)

    def send(self, alert: dict) -> bool:
        if not self.enabled or not self.webhook_url:
            return True

        color = "#FF0000" if alert.get("severity") == "HIGH" else "#FFA500"
        payload = {
            "attachments": [{
                "color": color,
                "title": f"Moodle Anomaly - {alert.get('severity')}",
                "fields": [
                    {"title": "Time",          "value": alert.get("datetime", "N/A"), "short": True},
                    {"title": "Score",         "value": f"{alert.get('final_score', 0):.3f}", "short": True},
                    {"title": "Request Count", "value": str(alert.get("metrics", {}).get("request_count", "N/A")), "short": True},
                ],
                "text": f"Users affected: {alert.get('metrics', {}).get('unique_users', 'N/A')}",
            }]
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info("Alerta enviada a Slack, respuesta: %s", resp.status)
                return resp.status < 400
        except Exception as exc:
            logger.error("Error al enviar alerta a Slack: %s", exc)
            return False
