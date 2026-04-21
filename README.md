# Sistema de Detección Temprana de Anomalías en el EVA Moodle (UCI)

## Descripción

Sistema híbrido de detección de anomalías en el Entorno Virtual de Aprendizaje (EVA) Moodle de la
Universidad de las Ciencias Informáticas (UCI), desarrollado siguiendo el método *Architecture-Based
Design* (ABD).

**Integración real** con el servidor Moodle existente (MySQL), **Redis Streams** como broker de
mensajería ligero, y **Grafana + InfluxDB** como panel de visualización.

Combina tres modelos analíticos complementarios:
- **ARIMA** (patrón lineal y estacional)
- **LSTM Autoencoder** (dependencias temporales no lineales)
- **Isolation Forest** (detección no supervisada sin datos etiquetados)

Los resultados se fusionan mediante votación ponderada y se visualizan en tiempo real vía
**Grafana + InfluxDB**.

---

## Arquitectura

```
Moodle DB (MySQL)
      │  TCP/IP – lectura directa de mdl_logstore_standard_log
      ▼
┌─────────────────┐     ┌───────────────────────────────┐
│  Log Capture    │────▶│  Redis Stream "moodle_logs"   │
│  (RF1)          │     │  (broker ligero, sin broker)  │
└─────────────────┘     └───────────────────────────────┘
                                         │
                                         ▼
                              ┌──────────────────────┐
                              │   Preprocessor (RF2) │
                              │   ventana 60 s       │
                              └──────────────────────┘
                                         │
                              ┌──────────────────────┐
                              │  Redis Stream        │
                              │  "moodle_timeseries" │
                              └──────────────────────┘
                                         │
               ┌─────────────────────────┼─────────────────────────┐
               ▼                         ▼                         ▼
        ┌────────────┐          ┌──────────────┐        ┌──────────────────┐
        │ ARIMA (RF3)│          │ LSTM  (RF4)  │        │ Isoforest  (RF5) │
        └────────────┘          └──────────────┘        └──────────────────┘
               │                         │                         │
               └─────────────────────────▼─────────────────────────┘
                                 ┌───────────────┐
                                 │ Fusion (RF6)  │
                                 └───────────────┘
                                         │
                      ┌──────────────────┴──────────────────┐
                      ▼                                     ▼
             ┌──────────────┐                    ┌──────────────────┐
             │ Alert Mgr    │                    │  InfluxDB Writer │
             │ (RF7)        │                    │  (RF8)           │
             └──────────────┘                    └──────────────────┘
                                                          │
                                                          ▼
                                                   ┌──────────────┐
                                                   │   Grafana    │
                                                   │  Dashboard   │
                                                   └──────────────┘
```

---

## Requisitos previos

| Servicio    | Versión mínima | Instalación                                   |
|-------------|----------------|-----------------------------------------------|
| Python      | 3.10+          | https://python.org                            |
| Docker      | 24+            | Para Redis + InfluxDB + Grafana               |
| MySQL Moodle| 5.7+ / 8.x     | Servidor Moodle existente (acceso de lectura) |

---

## Instalación

### 1. Clonar/descargar el proyecto y crear entorno virtual

```bat
cd moodle_anomaly_detection
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Levantar la infraestructura (Redis + InfluxDB + Grafana)

```bat
run.bat infra-up
```

Esto inicia con Docker Compose:
- **Redis 7** en `localhost:6379`
- **InfluxDB 2.7** en `http://localhost:8086`
- **Grafana 10** en `http://localhost:3000` (admin / admin)

El dashboard de Grafana se carga automáticamente gracias al provisionamiento incluido.

### 3. Configurar la conexión a Moodle

Editar `config/config.yaml`:

```yaml
moodle:
  host: "IP_DEL_SERVIDOR_MOODLE"   # servidor MySQL de Moodle
  database: "moodle"
  user: "moodle_reader"            # usuario solo lectura
  password: "tu_password"
```

*El token de InfluxDB y la config de Redis ya coinciden con el docker-compose incluido.*

### 4. Verificar todas las conexiones

```bat
run.bat check
# o directamente:
python scripts/check_connections.py
```

### 5. Ejecutar el sistema

```bat
run.bat
# o directamente:
python main.py
```

---

## Scripts Python disponibles

| Script | Descripción |
|--------|-------------|
| `python main.py` | Sistema completo (captura + preprocesado + análisis) |
| `python scripts/check_connections.py` | Diagnóstico: verifica Redis, Moodle DB e InfluxDB |
| `python scripts/setup_influxdb.py` | Configura InfluxDB (bucket, org, token) |
| `python scripts/run_system.py --capture` | Solo captura de logs Moodle → Redis |
| `python scripts/run_system.py --preprocess` | Solo preprocesador |
| `python scripts/run_system.py --engine` | Solo motor analítico |

---

## Comandos del lanzador (run.bat)

```bat
run.bat              # sistema completo
run.bat check        # verificar conexiones
run.bat capture      # solo captura
run.bat preprocess   # solo preprocesador
run.bat engine       # solo motor analítico
run.bat infra-up     # levantar Redis + InfluxDB + Grafana
run.bat infra-down   # detener infraestructura Docker
run.bat setup-influx # configurar InfluxDB por primera vez
```

---

## Panel Grafana

Grafana se autoconfigura al iniciar via Docker Compose.  
Accede en **http://localhost:3000** (admin / admin).

El dashboard **"Moodle Anomaly Detection - UCI"** incluye:
- Tráfico en tiempo real (peticiones, usuarios únicos, errores)
- Scores de anomalía por modelo (ARIMA, LSTM, Isolation Forest, Fusión)
- Votos de detección por modelo
- Timeline de alertas por severidad (LOW / MEDIUM / HIGH)

---

## Estructura del proyecto

```
moodle_anomaly_detection/
├── config/
│   ├── config.yaml                     # Parámetros de conexión y modelos
│   └── academic_calendar.json          # Calendario académico UCI
├── capture/
│   └── log_capture.py                  # RF1: Captura Moodle MySQL → Redis Stream
├── preprocessing/
│   └── preprocessor.py                 # RF2: Preprocesamiento y agregación
├── models/
│   ├── arima_model.py                  # RF3: SARIMA
│   ├── lstm_model.py                   # RF4: Autoencoder LSTM
│   ├── anomaly_detector.py             # RF5+RF6: Isolation Forest + fusión
│   └── saved/                          # Modelos persistidos
├── alerts/
│   └── alert_manager.py                # RF7: Alertas adaptativas
├── dashboard/
│   ├── influx_writer.py                # RF8: Escritura InfluxDB
│   ├── dashboards/
│   │   └── moodle_anomaly.json         # Dashboard Grafana preconfigurado
│   └── grafana_provisioning/           # Provisionamiento automático Grafana
│       ├── datasources/influxdb.yaml
│       └── dashboards/dashboard.yaml
├── scripts/
│   ├── check_connections.py            # Verificación de todos los servicios
│   ├── setup_influxdb.py               # Setup inicial InfluxDB
│   └── run_system.py                   # Lanzador con opciones por subsistema
├── utils/
│   └── config_loader.py
├── logs/                               # Logs del sistema y alertas
├── docker-compose.yml                  # Redis + InfluxDB + Grafana
├── run.bat                             # Lanzador Windows
├── main.py                             # Orquestador principal
└── requirements.txt
```

---

## Drivers arquitectónicos implementados

| Driver | Componente |
|--------|------------|
| Integración real con Moodle | Lectura directa `mdl_logstore_standard_log` vía MySQL |
| Broker ligero | Redis Streams (sin instalación compleja, sin Kafka) |
| Visualización | Grafana + InfluxDB con dashboard preconfigurado |
| Procesamiento en tiempo real | Redis Streams + agregación por ventanas de 60 s |
| Escalabilidad analítica | Modelos desacoplados en hilos independientes |
| Alta disponibilidad | Reconexión automática a MySQL si se pierde conexión |
| Modificabilidad | Cada modelo es un módulo Python intercambiable |

---

## Estado de pruebas (verificado el 2026-03-01)

### Módulos Python — 8/8 importan correctamente

```
  OK  utils.config_loader.load_config
  OK  models.anomaly_detector.AnomalyDetector
  OK  models.arima_model.ARIMAModel
  OK  models.lstm_model.LSTMModel
  OK  alerts.alert_manager.AlertManager
  OK  preprocessing.preprocessor.Preprocessor
  OK  dashboard.influx_writer.InfluxDBWriter
  OK  capture.log_capture.MoodleLogCapture
```

Entorno verificado: **Python 3.12.6**, **TensorFlow 2.20.0**, **numpy 2.2.6**, **pandas 2.2.2**,
**statsmodels 0.14.2**, **scikit-learn 1.4.2**, **redis-py 5.0.3**, **influxdb-client 1.43.0**.

### Simulación offline — pipeline completo sin servicios externos

Ejecutada con `python test_simulation.py --samples 300`:

```
Dataset: 300 ventanas temporales | Anomalías reales: 15 (5.0%)
Tiempo de ejecución: 26.8 segundos

Modelo                  Precision   Recall     F1      TP   FP   FN
────────────────────────────────────────────────────────────────────
ARIMA                     0.145    0.733    0.242     11   65    4
LSTM Autoencoder          0.065    1.000    0.122     15  216    0
Isolation Forest          0.097    0.800    0.173     12  112    3
Fusión ponderada (final)  0.110    0.933    0.197     14  113    1
```

**La fusión detecta 14 de 15 anomalías (Recall = 93.3%)** con solo 300 muestras de entrenamiento
inicial. Los falsos positivos disminuirán con historial real de Moodle, ya que los modelos
ajustan sus umbrales de forma continua.

> Para ejecutar la simulación:
> ```bat
> python test_simulation.py --samples 300
> python test_simulation.py --no-lstm --samples 100   # modo rápido
> ```
> Los resultados se guardan en `logs/simulation_results.csv`.

### Verificación de servicios externos

Resultado del script `python scripts/check_connections.py`:

```
[1/3] Redis (broker de mensajería)
  ✓ OK (v7.4.8)
[2/3] Moodle MySQL Database
  ✘  Moodle DB: no conecta a localhost:3306 — apuntar a host real del servidor Moodle

[3/3] InfluxDB (series temporales)
  ✓ OK (v2.7.12, bucket moodle_metrics)

Resultado: 2/3 servicios OK
```

Los servicios de infraestructura (Redis, InfluxDB, Grafana) deben iniciarse antes de ejecutar
el sistema completo. Ver sección **Activar el sistema completo** a continuación.

---

## Activar el sistema completo

Elige una opción según los recursos disponibles:

### Opción A — Docker Desktop (recomendada)

```bat
# 1. Instalar Docker Desktop desde https://docs.docker.com/desktop/install/windows-install/
# 2. Levantar todos los servicios con un solo comando:
run.bat infra-up

# Servicios disponibles tras el arranque:
#   Redis    → localhost:6379
#   InfluxDB → http://localhost:8086  (token: moodle-influx-token-2024)
#   Grafana  → http://localhost:3000  (admin / admin)

# 3. Verificar conexiones
run.bat check

# 4. Iniciar el sistema completo
run.bat
```

### Opción B — Redis portable para Windows (sin Docker)

1. Descargar `Redis-x64-*.zip` desde [github.com/microsoftarchive/redis/releases](https://github.com/microsoftarchive/redis/releases)
2. Descomprimir y ejecutar `redis-server.exe`
3. InfluxDB portable: descargar desde [influxdata.com/downloads](https://www.influxdata.com/downloads/) y ejecutar `influxd.exe`
4. Configurar InfluxDB con `python scripts/setup_influxdb.py`

### Opción C — Servicios en servidor remoto

Editar `config/config.yaml` apuntando a los hosts remotos donde estén instalados:

```yaml
moodle:
  host: "192.168.X.X"      # IP real del servidor Moodle
  user: "moodle_reader"
  password: "password_real"

redis:
  host: "192.168.X.X"      # IP del servidor Redis

influxdb:
  url: "http://192.168.X.X:8086"
  token: "token_generado_en_influxdb"
```

Luego:
```bat
run.bat check    # verificar todas las conexiones
run.bat          # iniciar el sistema
```
