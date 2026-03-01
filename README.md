# Sistema de Deteccion Temprana de Anomalias en el EVA Moodle (UCI)

## Descripcion

Sistema hibrido de deteccion de anomalias en el Entorno Virtual de Aprendizaje (EVA) Moodle de la Universidad de las Ciencias Informaticas (UCI), desarrollado siguiendo el metodo *Architecture-Based Design* (ABD).

Combina tres modelos analiticos complementarios:
- **ARIMA** (patron lineal y estacional)
- **LSTM Autoencoder** (dependencias temporales no lineales)  
- **Isolation Forest** (deteccion no supervisada sin datos etiquetados)

Los resultados se fusionan mediante votacion ponderada y se visualizan en tiempo real via **Grafana + InfluxDB**.

---

## Arquitectura

```
Moodle DB (MySQL)
      │  TCP/IP
      ▼
┌─────────────────┐     ┌──────────────────────┐
│  Log Capture    │────▶│  Redis Stream (raw)  │
│  (RF1)          │     └──────────────────────┘
└─────────────────┘                │
                                   ▼
                        ┌──────────────────────┐
                        │   Preprocessor (RF2) │
                        │  ventana 60s         │
                        └──────────────────────┘
                                   │
                                   ▼
                        ┌──────────────────────┐
                        │  Redis Stream (ts)   │
                        └──────────────────────┘
                                   │
               ┌───────────────────┼───────────────────┐
               ▼                   ▼                   ▼
        ┌────────────┐     ┌─────────────┐    ┌──────────────────┐
        │ ARIMA (RF3)│     │ LSTM  (RF4) │    │ Isoforest  (RF5) │
        └────────────┘     └─────────────┘    └──────────────────┘
               │                   │                   │
               └───────────────────▼───────────────────┘
                           ┌───────────────┐
                           │ Fusion (RF6)  │
                           └───────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
           ┌──────────────┐              ┌──────────────────┐
           │ Alert Mgr    │              │  InfluxDB Writer │
           │ (RF7)        │              │  (RF8)           │
           └──────────────┘              └──────────────────┘
                                                 │
                                                 ▼
                                          ┌──────────────┐
                                          │   Grafana    │
                                          │  Dashboard   │
                                          └──────────────┘
```

---

## Requisitos previos

| Servicio    | Version minima | Notas                                      |
|-------------|----------------|--------------------------------------------|
| Python      | 3.10+          |                                            |
| Redis       | 7.x            | Instalacion local o remota                 |
| InfluxDB    | 2.x            | Crear bucket `moodle_metrics` y org `UCI`  |
| Grafana     | 10.x           | Configurar datasource InfluxDB             |
| MySQL       | 5.7+ / 8.x     | Base de datos de Moodle                    |

---

## Instalacion

```bash
# 1. Crear entorno virtual
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/Mac

# 2. Instalar dependencias
pip install -r requirements.txt
```

---

## Configuracion

Editar `config/config.yaml`:

```yaml
moodle:
  host: "IP_DEL_SERVIDOR_MOODLE"
  database: "moodle"
  user: "moodle_user"
  password: "tu_password"

redis:
  host: "localhost"

influxdb:
  url: "http://localhost:8086"
  token: "TOKEN_GENERADO_EN_INFLUXDB"
  org: "UCI"
  bucket: "moodle_metrics"
```

---

## Ejecucion

```bash
python main.py
```

El sistema inicia tres hilos en paralelo:
1. **Capture** – lee logs de Moodle y los publica en Redis
2. **Preprocessor** – agrega en ventanas de 60 s y serializa como series temporales
3. **Analytics** – entrena y evalua los tres modelos, escribe en InfluxDB y genera alertas

---

## Configuracion de Grafana

1. Agregar datasource **InfluxDB** apuntando a `http://localhost:8086`
2. Importar paneles para las mediciones:
   - `moodle_traffic` → metricas de trafico en tiempo real
   - `anomaly_scores` → scores de anomalia por modelo y score fusionado
   - `alerts`         → timeline de alertas por severidad

---

## Estructura del proyecto

```
moodle_anomaly_detection/
├── config/
│   ├── config.yaml               # Parametros de conexion y modelos
│   └── academic_calendar.json    # Calendario academico UCI 2025-2026
├── capture/
│   └── log_capture.py            # RF1: Captura de logs Moodle (MySQL → Redis)
├── preprocessing/
│   └── preprocessor.py           # RF2: Preprocesamiento y agregacion temporal
├── models/
│   ├── arima_model.py            # RF3: Modelo SARIMA
│   ├── lstm_model.py             # RF4: Autoencoder LSTM
│   ├── anomaly_detector.py       # RF5+RF6: Isolation Forest + fusion
│   └── saved/                    # Modelos persistidos en disco
├── alerts/
│   └── alert_manager.py          # RF7: Alertas con umbrales adaptativos
├── dashboard/
│   └── influx_writer.py          # RF8: Escritura en InfluxDB para Grafana
├── utils/
│   └── config_loader.py
├── logs/                         # Logs del sistema y alertas
├── main.py                       # Orquestador principal
└── requirements.txt
```

---

## Drivers arquitectonicos implementados

| Driver                    | Componente                                    |
|---------------------------|-----------------------------------------------|
| Procesamiento en tiempo real | Redis Streams + agregacion por ventanas    |
| Escalabilidad analitica   | Modelos desacoplados en hilos independientes  |
| Alta disponibilidad       | Hilos daemon con reconexion automatica        |
| Modificabilidad           | Cada modelo es un modulo Python intercambiable|
| Seguridad                 | HTTPS en Grafana, token en InfluxDB           |
| Integracion con Moodle    | Lectura directa de `mdl_logstore_standard_log`|
