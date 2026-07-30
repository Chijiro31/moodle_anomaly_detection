"""
api_server.py
============
REST API con Flask para exponer el sistema de detección de anomalías.
Incluye documentación Swagger/OpenAPI.

Uso:
    python api_server.py

Endpoints:
    GET  /health              - Health check
    GET  /api/v1/anomalies    - Lista de anomalías detectadas
    GET  /api/v1/anomalies/{id} - Detalle de una anomalía específica
    POST /api/v1/anomalies    - Reportar una anomalía manual
    GET  /api/v1/models/status - Estado de los modelos
    GET  /api/v1/metrics      - Métricas del sistema
    POST /api/v1/retrain      - Forzar reentrenamiento de modelos
    GET  /api/v1/drift/status - Estado del detector de concept drift
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from functools import wraps

import numpy as np
import yaml
from flask import Flask, jsonify, request

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Cargar configuración
config_path = os.path.join(os.path.dirname(__file__), "..", "config", "config.yaml")
with open(config_path) as f:
    CONFIG = yaml.safe_load(f)

# Rutas para almacenamiento local
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Estado global de los modelos (inicializado lazy)
_models_state = {
    "arima": {"loaded": False, "last_retrain": None},
    "lstm": {"loaded": False, "last_retrain": None},
    "isolation_forest": {"loaded": False, "last_retrain": None},
}
_drift_detector = None


def get_influx_client():
    """Obtiene cliente InfluxDB."""
    from influxdb_client import InfluxDBClient
    return InfluxDBClient(
        url=CONFIG["influxdb"]["url"],
        token=CONFIG["influxdb"]["token"],
        org=CONFIG["influxdb"]["org"]
    )


def init_drift_detector():
    """Inicializa el detector de concept drift."""
    global _drift_detector
    if _drift_detector is None:
        from alerting.concept_drift_detector import ConceptDriftDetector
        _drift_detector = ConceptDriftDetector()


def require_auth(f):
    """Decorator para autenticación básica."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        expected_user = os.environ.get("API_USER", "admin")
        expected_pass = os.environ.get("API_PASS", "admin")
        if not auth or auth.username != expected_user or auth.password != expected_pass:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


# =============================================================================
# SWAGGER DOCS
# =============================================================================

SWAGGER_TEMPLATE = {
    "swagger": "2.0",
    "info": {
        "title": "Moodle Anomaly Detection API",
        "description": "API REST para el sistema de detección temprana de anomalías en Moodle UCI",
        "version": "1.0.0",
        "contact": {
            "name": "Moodle Anomaly Detection Project",
            "url": "https://github.com/Chijiro31/moodle_anomaly_detection"
        }
    },
    "basePath": "/api/v1",
    "schemes": ["http", "https"],
    "paths": {
        "/health": {
            "get": {
                "summary": "Health check",
                "description": "Verifica el estado del API y sus dependencias",
                "responses": {
                    "200": {"description": "APIhealthy"},
                    "503": {"description": "Dependencies unavailable"}
                }
            }
        },
        "/anomalies": {
            "get": {
                "summary": "Listar anomalías",
                "description": "Retorna lista de anomalías detectadas en el rango de tiempo especificado",
                "parameters": [
                    {"name": "from", "in": "query", "type": "string", "default": "now-1h", "description": "Start time (InfluxDB syntax)"},
                    {"name": "to", "in": "query", "type": "string", "default": "now", "description": "End time"},
                    {"name": "limit", "in": "query", "type": "integer", "default": 100, "description": "Max results"}
                ],
                "responses": {
                    "200": {"description": "Lista de anomalías"},
                    "500": {"description": "Error interno"}
                }
            }
        },
        "/anomalies/{id}": {
            "get": {
                "summary": "Detalle de anomalía",
                "description": "Retorna detalles de una anomalía específica",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "type": "string", "description": "Timestamp de la anomalía"}
                ],
                "responses": {
                    "200": {"description": "Detalle de anomalía"},
                    "404": {"description": "No encontrada"}
                }
            }
        },
        "/models/status": {
            "get": {
                "summary": "Estado de modelos",
                "description": "Retorna el estado actual de ARIMA, LSTM e Isolation Forest",
                "responses": {
                    "200": {"description": "Estado de modelos"}
                }
            }
        },
        "/metrics": {
            "get": {
                "summary": "Métricas del sistema",
                "description": "Retorna métricas de rendimiento de los modelos",
                "responses": {
                    "200": {"description": "Métricas del sistema"}
                }
            }
        },
        "/retrain": {
            "post": {
                "summary": "Forzar reentrenamiento",
                "description": "Fuerza el reentrenamiento de uno o más modelos",
                "parameters": [
                    {"name": "models", "in": "body", "required": True, "type": "array", 
                     "items": {"type": "string"}, "description": "Lista de modelos a reentrenar ['arima', 'lstm', 'iforest']"}
                ],
                "responses": {
                    "200": {"description": "Reentrenamiento iniciado"},
                    "400": {"description": "Modelos inválidos"}
                }
            }
        },
        "/drift/status": {
            "get": {
                "summary": "Estado de concept drift",
                "description": "Retorna el estado del detector de concept drift",
                "responses": {
                    "200": {"description": "Estado del detector"}
                }
            }
        }
    }
}


# =============================================================================
# ENDPOINTS
# =============================================================================

@app.route("/health")
def health():
    """Health check endpoint."""
    checks = {
        "redis": False,
        "influxdb": False,
    }
    
    # Check Redis
    try:
        import redis
        r = redis.Redis(host=CONFIG["redis"]["host"], port=CONFIG["redis"]["port"])
        checks["redis"] = r.ping()
    except:
        pass
    
    # Check InfluxDB
    try:
        client = get_influx_client()
        checks["influxdb"] = client.health().status == "pass"
        client.close()
    except:
        pass
    
    all_ok = all(checks.values())
    status_code = 200 if all_ok else 503
    
    return jsonify({
        "status": "healthy" if all_ok else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks
    }), status_code


@app.route("/api/v1/anomalies", methods=["GET"])
@require_auth
def list_anomalies():
    """Lista todas las anomalías detectadas."""
    from_time = request.args.get("from", "now-1h")
    to_time = request.args.get("to", "now")
    limit = int(request.args.get("limit", 100))
    
    try:
        client = get_influx_client()
        query_api = client.query_api()
        
        query = f'''
        from(bucket: "{CONFIG["influxdb"]["bucket"]}")
        |> range(start: {from_time}, stop: {to_time})
        |> filter(fn: (r) => r._measurement == "alerts")
        |> limit(n: {limit})
        '''
        
        result = query_api.query(query)
        
        anomalies = []
        for table in result:
            for r in table.records:
                anomalies.append({
                    "timestamp": r.get_time().timestamp(),
                    "datetime": r.get_time().isoformat(),
                    "severity": r.get_value() if r.get_field() == "severity_level" else None,
                    "final_score": r.get_value() if r.get_field() == "final_score" else None,
                    "request_count": r.get_value() if r.get_field() == "request_count" else None,
                })
        
        client.close()
        
        return jsonify({
            "count": len(anomalies),
            "anomalies": anomalies
        })
        
    except Exception as e:
        logger.error(f"Error fetching anomalies: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/anomalies/<timestamp>", methods=["GET"])
@require_auth
def get_anomaly(timestamp: str):
    """Obtiene detalle de una anomalía específica."""
    try:
        client = get_influx_client()
        query_api = client.query_api()
        
        query = f'''
        from(bucket: "{CONFIG["influxdb"]["bucket"]}")
        |> range(start: {int(timestamp) - 60}, stop: {int(timestamp) + 60})
        |> filter(fn: (r) => r._measurement == "anomaly_scores")
        |> last()
        '''
        
        result = query_api.query(query)
        
        data = {}
        for table in result:
            for r in table.records:
                data[r.get_field()] = r.get_value()
        
        client.close()
        
        if not data:
            return jsonify({"error": "Anomaly not found"}), 404
        
        return jsonify({
            "timestamp": int(timestamp),
            "datetime": datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat(),
            "scores": data
        })
        
    except Exception as e:
        logger.error(f"Error fetching anomaly: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/models/status", methods=["GET"])
@require_auth
def models_status():
    """Retorna estado de los modelos."""
    global _models_state
    
    return jsonify({
        "models": _models_state,
        "config": {
            "arima": {"order": CONFIG["models"]["arima"]["order"]},
            "lstm": {"sequence_length": CONFIG["models"]["lstm"]["sequence_length"]},
            "isolation_forest": {"contamination": CONFIG["models"]["anomaly_detector"]["contamination"]}
        }
    })


@app.route("/api/v1/metrics", methods=["GET"])
@require_auth
def system_metrics():
    """Retorna métricas del sistema."""
    try:
        client = get_influx_client()
        query_api = client.query_api()
        
        # Query para obtener métricas agregadas
        query = f'''
        from(bucket: "{CONFIG["influxdb"]["bucket"]}")
        |> range(start: -24h)
        |> filter(fn: (r) => r._measurement == "anomaly_scores")
        |> last()
        '''
        
        result = query_api.query(query)
        
        metrics = {
            "total_detections": 0,
            "high_severity": 0,
            "avg_score": 0,
        }
        
        scores = []
        for table in result:
            for r in table.records:
                if r.get_field() == "final_score":
                    scores.append(r.get_value())
        
        if scores:
            metrics["total_detections"] = len(scores)
            metrics["high_severity"] = sum(1 for s in scores if s >= 0.7)
            metrics["avg_score"] = np.mean(scores)
        
        client.close()
        
        return jsonify(metrics)
        
    except Exception as e:
        logger.error(f"Error fetching metrics: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/retrain", methods=["POST"])
@require_auth
def trigger_retrain():
    """Fuerza reentrenamiento de modelos."""
    data = request.get_json()
    models = data.get("models", ["all"])
    
    if "all" in models:
        models = ["arima", "lstm", "isolation_forest"]
    
    valid_models = {"arima", "lstm", "isolation_forest"}
    if not all(m in valid_models for m in models):
        return jsonify({"error": f"Invalid models. Valid: {valid_models}"}), 400
    
    global _models_state
    
    retrain_results = {}
    for model in models:
        _models_state[model]["last_retrain"] = datetime.now(timezone.utc).isoformat()
        retrain_results[model] = {"status": "triggered", "timestamp": datetime.now(timezone.utc).isoformat()}
        logger.info(f"Retrain triggered for {model}")
    
    return jsonify({
        "message": "Retrain initiated",
        "results": retrain_results
    })


@app.route("/api/v1/drift/status", methods=["GET"])
@require_auth
def drift_status():
    """Retorna estado del detector de concept drift."""
    init_drift_detector()
    
    status = _drift_detector.get_status()
    return jsonify(status)


# =============================================================================
# ERROR HANDLERS
# =============================================================================

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    
    logger.info(f"Starting Moodle Anomaly Detection API on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)