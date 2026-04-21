"""
API Documentation
================
REST API para el sistema de detección de anomalías Moodle.

Base URL: http://localhost:5000/api/v1

Autenticación
------------
La API usa autenticación básica HTTP.
Credenciales por defecto: admin/admin (cambiar en config.yaml para producción)

Headers requeridos:
    Authorization: Basic <base64(username:password)>

Endpoints
--------

GET /health
    Verifica el estado del API y sus dependencias.
    Response:
        {
            "status": "healthy|degraded",
            "timestamp": "2026-04-21T00:00:00+00:00",
            "checks": {"redis": true, "influxdb": true}
        }

GET /api/v1/anomalies
    Lista todas las anomalías detectadas.
    Query params:
        - from: Start time (default: now-1h)
        - to: End time (default: now)
        - limit: Max results (default: 100)
    Response:
        {
            "count": 42,
            "anomalies": [
                {
                    "timestamp": 1776732548,
                    "datetime": "2026-04-21T00:02:28+00:00",
                    "severity": 2,
                    "final_score": 0.733,
                    "request_count": 620
                },
                ...
            ]
        }

GET /api/v1/anomalies/{timestamp}
    Obtiene detalle de una anomalía específica.
    Response:
        {
            "timestamp": 1776732548,
            "datetime": "2026-04-21T00:02:28+00:00",
            "scores": {
                "final_score": 0.733,
                "arima_anomaly": 1,
                "lstm_anomaly": 0,
                "if_anomaly": 1,
                ...
            }
        }

GET /api/v1/models/status
    Retorna el estado actual de los modelos.
    Response:
        {
            "models": {
                "arima": {"loaded": true, "last_retrain": "2026-04-21T00:00:00+00:00"},
                "lstm": {"loaded": true, "last_retrain": "2026-04-21T00:00:00+00:00"},
                "isolation_forest": {"loaded": true, "last_retrain": "2026-04-21T00:00:00+00:00"}
            },
            "config": {...}
        }

GET /api/v1/metrics
    Retorna métricas agregadas del sistema.
    Response:
        {
            "total_detections": 150,
            "high_severity": 12,
            "avg_score": 0.523
        }

POST /api/v1/retrain
    Fuerza el reentrenamiento de modelos.
    Body:
        {"models": ["arima", "lstm"]}  # o ["all"]
    Response:
        {
            "message": "Retrain initiated",
            "results": {
                "arima": {"status": "triggered", "timestamp": "2026-04-21T00:00:00+00:00"},
                "lstm": {"status": "triggered", "timestamp": "2026-04-21T00:00:00+00:00"}
            }
        }

GET /api/v1/drift/status
    Retorna el estado del detector de concept drift.
    Response:
        {
            "drift_detected": false,
            "last_drift_time": "2026-04-21T00:00:00+00:00",
            "drift_count": 0,
            "samples_processed": 1542
        }

Ejemplos
--------

# Health check
curl -u admin:admin http://localhost:5000/health

# List anomalies
curl -u admin:admin "http://localhost:5000/api/v1/anomalies?from=now-24h&limit=10"

# Get model status
curl -u admin:admin http://localhost:5000/api/v1/models/status

# Trigger retrain
curl -u admin:admin -X POST -H "Content-Type: application/json" \
    -d '{"models": ["arima"]}' \
    http://localhost:5000/api/v1/retrain

# Check concept drift
curl -u admin:admin http://localhost:5000/api/v1/drift/status

Errores
-------
401 Unauthorized - Credenciales inválidas
404 Not Found - Recurso no encontrado
500 Internal Server Error - Error en el servidor

Swagger UI
----------
Disponible en: http://localhost:5000/docs
(Requiere flask-swagger-ui instalado)
"""

__version__ = "1.0.0"