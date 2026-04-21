"""
alert_manager.py
=============
Sistema de alertas activas con soporte para múltiples canales:
- Email (SMTP)
- Webhook (HTTP POST)
- Slack (via webhook)
- Log (archivo local)

Configurado en config/config.yaml bajo la sección "alerts"
"""

import logging
import smtplib
import json
import urllib.request
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class AlertChannel:
    """Base class para canales de alerta."""
    
    def send(self, alert: dict) -> bool:
        raise NotImplementedError


class EmailChannel(AlertChannel):
    """Canal de alertas por email via SMTP."""
    
    def __init__(self, config: dict):
        self.host = config.get("smtp_host", "localhost")
        self.port = config.get("smtp_port", 587)
        self.sender = config.get("sender", "monitor@localhost")
        self.recipients = config.get("recipients", [])
        self.enabled = config.get("enabled", False)
    
    def send(self, alert: dict) -> bool:
        if not self.enabled:
            return True
        
        severity = alert.get("severity", "MEDIUM")
        subject = f"[{severity}] Moodle Anomaly Alert - {alert.get('datetime', '')}"
        
        body = self._format_body(alert)
        
        try:
            msg = MIMEMultipart()
            msg["From"] = self.sender
            msg["To"] = ", ".join(self.recipients)
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "html"))
            
            with smtplib.SMTP(self.host, self.port) as server:
                server.starttls()
                server.send_message(msg)
            
            logger.info(f"Email alert sent to {self.recipients}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")
            return False
    
    def _format_body(self, alert: dict) -> str:
        return f"""
        <html>
        <body>
            <h2 style="color: {'red' if alert.get('severity') == 'HIGH' else 'orange'}">
                Moodle Anomaly Detected
            </h2>
            <table style="border-collapse: collapse;">
                <tr><td style="padding: 8px;"><b>Time:</b></td><td style="padding: 8px;">{alert.get('datetime')}</td></tr>
                <tr><td style="padding: 8px;"><b>Severity:</b></td><td style="padding: 8px;">{alert.get('severity')}</td></tr>
                <tr><td style="padding: 8px;"><b>Final Score:</b></td><td style="padding: 8px;">{alert.get('final_score', 0):.3f}</td></tr>
                <tr><td style="padding: 8px;"><b>Request Count:</b></td><td style="padding: 8px;">{alert.get('metrics', {}).get('request_count', 'N/A')}</td></tr>
                <tr><td style="padding: 8px;"><b>Unique Users:</b></td><td style="padding: 8px;">{alert.get('metrics', {}).get('unique_users', 'N/A')}</td></tr>
            </table>
            <p style="margin-top: 20px;">
                <a href="http://localhost:3100/d/moodle-anomaly-v1">View Dashboard</a>
            </p>
        </body>
        </html>
        """


class WebhookChannel(AlertChannel):
    """Canal de alertas via HTTP Webhook."""
    
    def __init__(self, config: dict):
        self.url = config.get("url", "")
        self.enabled = config.get("enabled", False)
        self.headers = config.get("headers", {"Content-Type": "application/json"})
        self.method = config.get("method", "POST")
    
    def send(self, alert: dict) -> bool:
        if not self.enabled or not self.url:
            return True
        
        payload = {
            "event": "moodle_anomaly",
            "timestamp": alert.get("timestamp"),
            "datetime": alert.get("datetime"),
            "severity": alert.get("severity"),
            "final_score": alert.get("final_score", 0),
            "metrics": alert.get("metrics", {}),
            "votes": alert.get("votes", {}),
        }
        
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.url,
                data=data,
                headers=self.headers,
                method=self.method
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info(f"Webhook alert sent, response: {resp.status}")
                return resp.status < 400
        except Exception as e:
            logger.error(f"Failed to send webhook alert: {e}")
            return False


class SlackChannel(AlertChannel):
    """Canal de alertas para Slack via Incoming Webhook."""
    
    def __init__(self, config: dict):
        self.webhook_url = config.get("webhook_url", "")
        self.enabled = config.get("enabled", False)
    
    def send(self, alert: dict) -> bool:
        if not self.enabled or not self.webhook_url:
            return True
        
        color = "#FF0000" if alert.get("severity") == "HIGH" else "#FFA500"
        
        payload = {
            "attachments": [{
                "color": color,
                "title": f"Moodle Anomaly - {alert.get('severity')}",
                "fields": [
                    {"title": "Time", "value": alert.get("datetime", "N/A"), "short": True},
                    {"title": "Score", "value": f"{alert.get('final_score', 0):.3f}", "short": True},
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
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info(f"Slack webhook alert sent, response: {resp.status}")
                return resp.status < 400
        except Exception as e:
            logger.error(f"Failed to send Slack webhook alert: {e}")
            return False


class LogChannel(AlertChannel):
    """Canal de alertas por archivo de log."""
    
    def __init__(self, config: dict):
        self.file = config.get("file", "logs/alerts.log")
        self.enabled = config.get("enabled", True)
    
    def send(self, alert: dict) -> bool:
        if not self.enabled:
            return True
        
        try:
            import os
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            with open(self.file, "a") as f:
                f.write(f"{alert.get('datetime')},{alert.get('severity')},"
                       f"{alert.get('final_score')},{alert.get('metrics', {}).get('request_count')}\n")
            return True
        except Exception as e:
            logger.error(f"Failed to write log alert: {e}")
            return False


class AlertManager:
    """
    Gestor central de alertas que coordina múltiples canales.
    Implementa cooldown para evitar spam de alertas.
    """
    
    def __init__(self, config: dict):
        self.channels = []
        
        # Configurar canales disponibles
        channels_cfg = config.get("alerts", {}).get("channels", {})
        
        if "email" in channels_cfg:
            self.channels.append(EmailChannel(channels_cfg["email"]))
        
        if "webhook" in channels_cfg:
            self.channels.append(WebhookChannel(channels_cfg["webhook"]))
        
        if "slack" in channels_cfg:
            self.channels.append(SlackChannel(channels_cfg["slack"]))
        
        if "log" in channels_cfg:
            self.channels.append(LogChannel(channels_cfg["log"]))
        
        # Cooldown entre alertas del mismo tipo
        self.cooldown_seconds = config.get("alerts", {}).get("cooldown_minutes", 5) * 60
        self._last_alert_time = {}  # key: alert_type, value: timestamp
    
    def send_alert(self, alert: dict) -> bool:
        """
        Envía alerta a todos los canales activos.
        Respeta el cooldown configurado.
        """
        alert_type = alert.get("alert_type", "default")
        now = datetime.now(timezone.utc).timestamp()
        
        # Verificar cooldown
        if alert_type in self._last_alert_time:
            elapsed = now - self._last_alert_time[alert_type]
            if elapsed < self.cooldown_seconds:
                logger.debug(f"Alert suppressed by cooldown: {alert_type}")
                return False
        
        # Enviar a todos los canales
        success = True
        for channel in self.channels:
            if not channel.send(alert):
                success = False
        
        # Actualizar cooldown
        if success:
            self._last_alert_time[alert_type] = now
        
        return success
    
    def send_test_alert(self) -> bool:
        """Envía una alerta de prueba."""
        return self.send_alert({
            "timestamp": datetime.now(timezone.utc).timestamp(),
            "datetime": datetime.now(timezone.utc).isoformat(),
            "severity": "TEST",
            "final_score": 0.5,
            "metrics": {"request_count": 100, "unique_users": 10},
            "alert_type": "test",
        })


class AdaptiveFusion:
    """
    Sistema de fusión adaptativa que ajusta los pesos de los modelos
    basándose en el rendimiento histórico.
    """
    
    def __init__(self, config: dict):
        self.weights = {
            "isolation_forest": 1.0,
            "arima": 0.8,
            "lstm": 1.2,
        }
        self._performance_window = 100  # últimas N detecciones
        self._recent_results = []
        
        # Pesos mínimos y máximos permitidos
        self.min_weight = 0.1
        self.max_weight = 2.0
    
    def update(self, model_name: str, was_correct: bool):
        """Actualiza el peso de un modelo basado en su rendimiento reciente."""
        self._recent_results.append({
            "model": model_name,
            "correct": was_correct,
            "timestamp": datetime.now(timezone.utc).timestamp()
        })
        
        # Mantener solo el window de resultados recientes
        if len(self._recent_results) > self._performance_window:
            self._recent_results.pop(0)
        
        # Recalcular peso del modelo
        self._adjust_weight(model_name)
    
    def _adjust_weight(self, model_name: str):
        """Ajusta el peso de un modelo específico."""
        model_results = [r for r in self._recent_results if r["model"] == model_name]
        if len(model_results) < 10:
            return  # No hay suficientes datos
        
        correct_rate = sum(1 for r in model_results if r["correct"]) / len(model_results)
        
        current_weight = self.weights.get(model_name, 1.0)
        
        if correct_rate > 0.7:
            # Modelo funcionando bien, aumentar peso
            new_weight = min(current_weight * 1.1, self.max_weight)
        elif correct_rate < 0.4:
            # Modelo fallando mucho, reducir peso
            new_weight = max(current_weight * 0.9, self.min_weight)
        
        self.weights[model_name] = new_weight
        logger.info(f"Adjusted {model_name} weight: {current_weight:.2f} -> {new_weight:.2f} (accuracy: {correct_rate:.2%})")
    
    def get_weights(self) -> dict:
        """Retorna los pesos actuales."""
        return self.weights.copy()
    
    def fuse(self, if_result: dict, arima_result: dict, lstm_result: dict) -> dict:
        """
        Fusión ponderada con pesos adaptativos.
        """
        votes = {
            "isolation_forest": 1.0 if if_result.get("is_anomaly", False) else 0.0,
            "arima": 1.0 if arima_result.get("is_anomaly", False) else 0.0,
            "lstm": 1.0 if lstm_result.get("is_anomaly", False) else 0.0,
        }
        
        total_weight = sum(self.weights.values())
        weighted_score = sum(votes[m] * self.weights[m] for m in votes) / total_weight
        is_anomaly = weighted_score >= 0.5
        
        return {
            "final_score": round(weighted_score, 4),
            "is_anomaly": is_anomaly,
            "votes": votes,
            "models_used": list(votes.keys()),
            "weights_used": self.weights.copy(),
        }