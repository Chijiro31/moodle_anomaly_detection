"""
test_anomaly_injection.py
========================
Script para generar anomalías controladas y verificar el sistema.

Los modelos necesitan primero aprender el comportamiento normal
antes de poder detectar anomalías. El script:
1. Genera datos NORMALES para entrenar los modelos
2. Inyecta anomalías conocidas (DROP, SPIKE, ERROR_BURST)
3. Verifica que los modelos las detecten
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
from datetime import datetime, timezone
from models.arima_model import ARIMAModel
from models.lstm_model import LSTMModel
from models.anomaly_detector import AnomalyDetector
from utils.config_loader import load_config
from dashboard.influx_writer import InfluxDBWriter

def generate_normal_data(n_samples=100):
    """Genera datos con patrón normal de Moodle."""
    rng = np.random.default_rng(42)
    data = []
    base = 400.0
    for i in range(n_samples):
        factor = 1.0 + 0.2 * np.sin(i * 0.1)  # Variación suave
        req = base * factor + rng.normal(0, 20)
        users = 40 + rng.normal(0, 5)
        err = rng.poisson(2)
        data.append({
            "request_count": max(100, req),
            "unique_users": max(10, int(users)),
            "error_count": max(0, int(err)),
            "course_count": 10
        })
    return data

def inject_anomaly_test_data():
    """Inyecta puntos que sabemos que causan anomalías."""
    config = load_config('config/config.yaml')
    influx = InfluxDBWriter(config)

    arima = ARIMAModel(config)
    lstm = LSTMModel(config)
    iforest = AnomalyDetector(config)

    print("=" * 70)
    print("PASO 1: ENTRENANDO MODELOS CON DATOS NORMALES")
    print("=" * 70)
    
    # Generar y procesar datos normales para entrenar
    now_ts = int(datetime.now(timezone.utc).timestamp())
    normal_data = generate_normal_data(150)  # 150 puntos normales
    
    for i, point in enumerate(normal_data):
        ts = now_ts - (len(normal_data) - i) * 60  # 1 min apart
        
        arima.update(ts, point["request_count"])
        lstm.update(point)
        iforest.update(point)
        
        # Escribir a InfluxDB
        influx.write_traffic(ts, point)
        arima_res = arima._last_result if hasattr(arima, '_last_result') else {}
        lstm_res = lstm._no_result("") if hasattr(lstm, '_no_result') else {}
        iforest_res = iforest._no_result("") if hasattr(iforest, '_no_result') else {}
        
        if arima_res and lstm_res and iforest_res:
            fusion = AnomalyDetector.fuse(iforest_res, arima_res, lstm_res)
            influx.write_scores(ts, fusion, arima_res, lstm_res, iforest_res)
    
    print(f"  {len(normal_data)} puntos normales procesados")
    print(f"  ARIMA entrenado con {len(arima._history)} muestras")
    
    # Ahora probar con anomalías
    test_cases = [
        {
            "name": "CAÍDA SEVERA de tráfico (DROP)",
            "point": {"request_count": 20.0, "unique_users": 3, "error_count": 0, "course_count": 5},
            "expected_anomaly": True,
            "reason": "request_count 20 vs patrón normal ~400"
        },
        {
            "name": "PICO EXTREMO de tráfico (SPIKE)",
            "point": {"request_count": 5000.0, "unique_users": 200, "error_count": 30, "course_count": 50},
            "expected_anomaly": True,
            "reason": "request_count 5000 vs patrón normal ~400"
        },
        {
            "name": "BURST de errores HTTP",
            "point": {"request_count": 450.0, "unique_users": 45, "error_count": 80, "course_count": 15},
            "expected_anomaly": True,
            "reason": "error_count 80 vs patrón normal ~2"
        },
        {
            "name": "VALOR NORMAL - sin anomalía",
            "point": {"request_count": 380.0, "unique_users": 38, "error_count": 3, "course_count": 10},
            "expected_anomaly": False,
            "reason": "dentro del rango normal"
        },
    ]

    print("\n" + "=" * 70)
    print("PASO 2: INYECTANDO ANOMALÍAS")
    print("=" * 70)

    results = []
    for i, tc in enumerate(test_cases):
        ts = now_ts + i * 60  # 1 min apart, futuro
        point = tc["point"]

        # Ejecutar modelos
        arima_res = arima.update(ts, point["request_count"])
        lstm_res = lstm.update(point)
        iforest_res = iforest.update(point)
        fusion = AnomalyDetector.fuse(iforest_res, arima_res, lstm_res)

        detected = "✓ SÍ" if fusion['is_anomaly'] else "✗ NO"
        expected = "✓" if fusion['is_anomaly'] == tc["expected_anomaly"] else "⚠️ FALLO"
        
        print(f"\n{tc['name']} {expected}")
        print(f"  Datos: req={point['request_count']}, users={point['unique_users']}, err={point['error_count']}")
        print(f"  Detectado: {detected}")
        print(f"  ARIMA: predicted={arima_res.get('predicted', 0):.1f}, is_anomaly={arima_res.get('is_anomaly')}")
        print(f"  LSTM: recon_error={lstm_res.get('reconstruction_error', 0):.4f}, is_anomaly={lstm_res.get('is_anomaly')}")
        print(f"  IF: score={iforest_res.get('score', 0):.4f}, is_anomaly={iforest_res.get('is_anomaly')}")
        print(f"  FUSIÓN: score={fusion['final_score']:.4f}, is_anomaly={fusion['is_anomaly']}")

        # Escribir a InfluxDB
        influx.write_traffic(ts, point)
        influx.write_scores(ts, fusion, arima_res, lstm_res, iforest_res)
        
        if fusion['is_anomaly']:
            influx.write_alert({
                "timestamp": ts,
                "severity": "HIGH",
                "final_score": fusion['final_score'],
                "datetime": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "metrics": point
            })

        results.append({
            "name": tc["name"],
            "expected": tc["expected_anomaly"],
            "detected": fusion['is_anomaly'],
            "match": fusion['is_anomaly'] == tc["expected_anomaly"]
        })

    influx.close()
    
    print("\n" + "=" * 70)
    print("RESUMEN")
    print("=" * 70)
    passed = sum(1 for r in results if r['match'])
    print(f"  Pruebas pasadas: {passed}/{len(results)}")
    for r in results:
        status = "✓" if r['match'] else "⚠️"
        print(f"  {status} {r['name']}: expected={r['expected']}, detected={r['detected']}")
    
    print("\nDatos escritos a InfluxDB.")
    print("Abre Grafana: http://localhost:3100/d/moodle-anomaly-v1")

if __name__ == "__main__":
    inject_anomaly_test_data()