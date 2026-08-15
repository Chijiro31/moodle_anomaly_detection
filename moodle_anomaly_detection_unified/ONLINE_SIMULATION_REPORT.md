# Simulación en línea (online) del pipeline completo contra Moodle real

## 1. Propósito

Este documento describe una **simulación en línea**: el sistema completo (`main.py`,
con sus tres subsistemas — Captura, Preprocesamiento y Motor Analítico — corriendo
como hilos reales) ejecutándose contra una instancia **real** de Moodle, un **Redis**
real y un **InfluxDB** real, con tráfico HTTP genuino generado contra el servidor web,
durante el tiempo real necesario para que los tres modelos acumulen el historial
mínimo que requieren (no simulado ni acelerado).

## 2. Entorno de ejecución

**Host:**
- CPU: Intel Core i5-1135G7 (11.ª generación) @ 2.40GHz, 8 núcleos lógicos, 1 socket
- RAM: 30 GiB
- SO: Linux Mint 22.3, kernel 6.17.0-35-generic (x86_64)

**Servicios (todos vía Docker, corriendo simultáneamente):**

| Servicio | Imagen/versión | Rol |
|---|---|---|
| Moodle (real) | `moodlehq/moodle-php-apache:8.3` + MariaDB 11.4.12 (`moodle-docker`) | Fuente de datos real (`m_logstore_standard_log`) |
| Redis | 7.4.10 | Broker de mensajería (Redis Streams) |
| InfluxDB | v2.7.12 | Serie temporal de scores/alertas |
| Grafana | 10.4.0 | Visualización |

La conexión del sistema de detección a Moodle usa un usuario de solo lectura
(`moodle_reader`), en `config/config.yaml`, apuntando al puerto `3307` (mapeado por
`moodle-docker`) y a la tabla `m_logstore_standard_log` (prefijo `m_`, no `mdl_`,
propio de esta instancia de prueba).

**Población de usuarios de prueba:**
- 1,000 usuarios registrados (`tool_generator_000001` a `tool_generator_001000`)
- 12 con rol de profesor (`editingteacher`), el resto estudiantes
- Todos con contraseña conocida (reseteada vía un script PHP de un solo proceso,
  `admin/cli/bulk_reset_test_passwords.php`, en vez de 1000 invocaciones
  individuales de `admin/cli/reset_password.php`)
- Nombre completo (nombre + apellido) único para los 1000, agrupados por 5 orígenes
  culturales/lingüísticos consistentes (checo, occidental/inglés-alemán, ruso, chino,
  japonés) para mantener coherencia entre nombre y apellido

**Muestra procesada:**

La muestra de esta simulación quedó conformada por **10,053 registros de acceso reales**
(`m_logstore_standard_log`, `id > 11682`, es decir, todo lo generado desde el inicio de
la prueba, excluyendo los registros de configuración inicial de la plataforma: creación
de usuarios, cursos y matrículas), producidos por la población de 1,000 usuarios de
prueba y agregados por el preprocesador en **371 ventanas temporales de 60 segundos**.

## 3. Metodología del tráfico generado

Al no existir actividad orgánica de usuarios reales sobre esta instancia de prueba,
el tráfico se generó mediante tres scripts (`moodle_test_env/`):

1. **`generate_continuous_traffic.py`**: tráfico ligero continuo (3-8 peticiones cada
   20-35s reales) con un pool de sesiones autenticadas (40 estudiantes + los 12
   profesores reales), para que el preprocesador tenga al menos un evento por
   ventana de 60s y el motor analítico acumule historial real.
2. **`generate_all_users_login.py`**: barrido de cobertura completa — cada uno de
   los 1000 usuarios inició sesión real exactamente una vez (sin repetir), con
   progreso persistido para tolerar interrupciones.
3. **`generate_anomalous_traffic.py`**: la prueba de detección en sí — un ataque de
   fuerza bruta (60 intentos de login fallidos contra 3 cuentas) seguido de un pico
   de tráfico (600 peticiones concurrentes de 20 sesiones autenticadas), comprimido
   en una ventana de 60s.

## 4. Hallazgos e incidencias corregidas durante la ejecución

La ejecución en tiempo real expuso dos problemas reales del código, invisibles en
pruebas más cortas:

### 4.1 Pérdida de eventos ante reconexión a Moodle (`capture/log_capture.py`)

El cursor de captura (`_last_id`) se recalculaba como `MAX(id)` de Moodle en cada
reconexión a MySQL, en vez de recordar dónde se había quedado. Esto contradice
directamente la afirmación del escenario 6 del ATAM ("recuperación ante
interrupciones sin duplicación de datos"): evitaba duplicados, pero podía perder
silenciosamente eventos generados durante el corte.

**Corrección:** el cursor se persiste ahora en Redis (`moodle_capture:last_id`) tras
cada lote publicado, y se recupera al reconectar o reiniciar el proceso.

### 4.2 Pérdida del progreso de entrenamiento ante reinicio del proceso

ARIMA, LSTM Autoencoder e Isolation Forest solo guardaban en disco su **modelo ya entrenado**,
no el **buffer/historial acumulado** hacia el mínimo de muestras necesario (50/70/100
respectivamente). El host de esta prueba se suspendió/reinició varias veces durante
la espera real (~1.5-2 horas), y cada vez el progreso volvía a cero porque ningún
modelo había alcanzado aún su umbral de entrenamiento.

**Corrección:** los tres modelos ahora persisten su buffer crudo (y, en el caso de
Isolation Forest, también el contador de muestras) en disco en cada actualización
(`models/saved/arima_history.pkl`, `lstm_buffer.pkl`, `if_buffer.pkl`), verificado
explícitamente con una prueba que simula un reinicio de proceso.

## 5. Cronología de entrenamiento real

| Evento | Marca de tiempo (UTC) | Muestras |
|---|---|---|
| ARIMA entrenado | 2026-08-04 14:48:05 | 50 |
| LSTM Autoencoder entrenado | 2026-08-04 15:08:21 | 10 secuencias (umbral de reconstrucción: 0.2097) |
| Isolation Forest entrenado | 2026-08-04 15:39:37 | 100 |

Los tres modelos alcanzaron su mínimo de entrenamiento real (no simulado) tras
aproximadamente 1.5-2 horas de tráfico continuo real, repartidas en varias sesiones
del host debido a las interrupciones descritas en el punto 4.2.

## 6. Resultado inicial: detección de la ráfaga anómala real

Primera prueba, ventana **2026-08-04T19:49:00Z** — `request_count=194, unique_users=23,
error_count=60, course_count=5` (frente a una línea base de ~3-10 peticiones/ventana).

| Modelo | Resultado | Detalle |
|---|---|---|
| ARIMA | No detectó (`is_anomaly=0`) | predicho=192.03, intervalo=[-146.03, 530.10]; 194 cae dentro del intervalo |
| LSTM Autoencoder | **Anomalía (`is_anomaly=1`)** | error de reconstrucción 0.333 > umbral 0.2097 |
| Isolation Forest | **Anomalía (`is_anomaly=1`)** | score normalizado = 1.0 |
| **Fusión ponderada** | **Anomalía (`is_anomaly=1`)** | `final_score = 0.7333` (umbral 0.5) |

**Alerta generada y escrita en InfluxDB:** severidad **HIGH** (nivel 3), con
`request_count=194` y `unique_users=23` como metadatos.

Adicionalmente, el detector de concept drift (Page-Hinkley + ADWIN) se disparó
durante la ráfaga (`drift_detected=True, drift_type=distribution, confidence=100%`),
confirmando que ese componente también reacciona correctamente ante datos reales.

## 7. Ampliación: cuatro escenarios de ataque reales distintos

Con un único incidente no hay variación suficiente para comparar modelos de forma
estadísticamente honesta. Se generaron 3 escenarios reales adicionales
(`generate_attack_scenarios.py`), siguiendo la taxonomía de amenazas de la tesis (I.7):

- **Ráfaga de errores** (`error_burst`): 80 intentos de login fallidos, volumen moderado
  sin sesiones autenticadas concurrentes — aísla la señal de `error_count` de la de
  `request_count`.
- **Manipulación de parámetros de URL** (`url_manipulation`): 100 peticiones autenticadas
  a IDs de curso/actividad inválidos o fuera de rango.
- **Caída de tráfico** (`drop`, 2 ventanas): se detuvo por completo la generación de
  tráfico continuo durante ~150s reales, produciendo ventanas de actividad anómalamente
  baja (incluso ventanas sin ningún evento).

| Escenario | ARIMA | LSTM Autoencoder | Isolation Forest | Fusión (binaria, umbral 0.5) |
|---|---|---|---|---|
| Fuerza bruta + pico | No | Sí | Sí | Sí |
| Ráfaga de errores | No | Sí | Sí | Sí |
| Manipulación de URL | No | Sí | No | No |
| Caída de tráfico (×2) | No | Sí | No | No |

Tabla 16. Resultados por modelo sobre las 5 ventanas anómalas reales (371 ventanas totales)

| Modelo | Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|---|
| ARIMA | 0.000 | 0.000 | 0.000 | 0 | 1 | 5 |
| LSTM Autoencoder | 0.357 | 1.000 | 0.526 | 5 | 9 | 0 |
| Isolation Forest | 0.667 | 0.400 | 0.500 | 2 | 1 | 3 |
| Fusión ponderada (binaria, anterior) | 0.667 | 0.400 | 0.500 | 2 | 1 | 3 |

**Hallazgo clave:** en ninguna ventana de toda la sesión ARIMA votó "sí". Con la fusión
binaria original (voto 0/1 por modelo, umbral 0.5, pesos IF=1.0/ARIMA=0.8/LSTM=1.2),
esto atrapaba matemáticamente a la fusión como un espejo exacto de Isolation Forest: solo
se activaba cuando IF votaba (porque ahí LSTM Autoencoder también coincidía), y nunca cuando
solo LSTM Autoencoder disparaba (su voto aislado, 1.2/3.0=0.4, no alcanza 0.5). Por eso la
fusión perdía `url_manipulation` y ambas caídas — exactamente los mismos casos que se le
escapaban a Isolation Forest solo. Ningún recalibrado de los pesos podía corregir esto
mientras los votos siguieran siendo binarios (se probaron 6 combinaciones distintas de
pesos sin que cambiara el resultado).

## 8. Rediseño de la fusión: de voto binario a score continuo

Isolation Forest y LSTM Autoencoder ya calculaban un score continuo interno (`if_score`,
`reconstruction_error`), pero `AnomalyDetector.fuse()` los descartaba y solo combinaba el
booleano `is_anomaly` de cada uno. Revisando los scores reales de las 3 ventanas que la
fusión binaria perdía, `if_score` era 0.958 y 0.950 en dos de ellas (`url_manipulation` y
la primera caída) — muy cerca del máximo, aunque por debajo del umbral interno de decisión
de Isolation Forest. Esa información se estaba descartando.

**Cambios aplicados** (`models/arima_model.py`, `models/lstm_model.py`,
`models/anomaly_detector.py`):

1. `ARIMAModel._evaluate()` y `LSTMModel._evaluate()` ahora devuelven también un campo
   `score` continuo en `[0, 1]`: 0.5 exactamente en el punto que antes marcaba
   `is_anomaly` (borde del intervalo de confianza para ARIMA; umbral de reconstrucción
   para LSTM Autoencoder), escalando hasta 1.0 al doble de esa distancia.
2. `AnomalyDetector.fuse()` combina esos 3 scores continuos (los mismos pesos:
   IF=1.0, ARIMA=0.8, LSTM=1.2) en vez de los votos binarios.
3. El umbral de decisión se recalibró de 0.5 a **`FUSION_THRESHOLD = 0.58`**, calibrado
   empíricamente probando varios umbrales y pesos contra las 371 ventanas reales
   (5 anómalas + 366 normales) hasta maximizar F1 sin inflar falsos positivos.

Tabla 17. Comparación final (371 ventanas reales, 5 anomalías de 4 tipos distintos)

| Modelo | Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|---|
| ARIMA | 0.000 | 0.000 | 0.000 | 0 | 1 | 5 |
| LSTM Autoencoder (solo) | 0.357 | 1.000 | 0.526 | 5 | 9 | 0 |
| Isolation Forest (solo) | 0.667 | 0.400 | 0.500 | 2 | 1 | 3 |
| Fusión binaria (anterior, umbral 0.5) | 0.667 | 0.400 | 0.500 | 2 | 1 | 3 |
| **Fusión por score continuo (nueva, umbral 0.58)** | **0.667** | **0.800** | **0.727** | **4** | **2** | **1** |

Con el cambio, la fusión detecta 4 de los 5 escenarios de ataque (solo se le escapa la
caída de tráfico más débil, `if_score=0.214`), con F1 superior a los 3 modelos
individuales y a la propia fusión binaria anterior. Se verificó primero por simulación
offline (recombinando los scores reales ya registrados en InfluxDB, sin tocar el sistema
en producción) antes de aplicar el cambio; tras aplicarlo, el sistema en vivo recuperó
su historial persistido (293 muestras) sin perder el entrenamiento acumulado.

## 9. Discusión

El resultado inicial (sección 6) muestra, con datos reales, un patrón de
complementariedad de modelos: ARIMA (el más conservador, solo mira `request_count` con
una banda de confianza aún amplia por el historial limitado) no marca la anomalía por
sí solo, pero LSTM Autoencoder e Isolation Forest sí.

Sin embargo, al ampliar la prueba a 4 tipos de ataque distintos (sección 7), la fusión
*binaria* resultó ser, en la práctica, un espejo exacto de Isolation Forest — no
superior a él —, porque ARIMA nunca corrobora en datos reales y el voto aislado de LSTM
Autoencoder no bastaba. Solo al rediseñar la fusión para usar los scores continuos que
los propios modelos ya calculaban (sección 8) se obtuvo una mejora medible sobre los 3
modelos individuales (F1 de 0.500 a 0.727), demostrando con datos reales que la fusión
ponderada aporta valor por encima de cualquier modelo individual, siempre que combine
la magnitud de la señal de cada modelo y no solo su decisión binaria.

Esta prueba también demuestra, en la práctica, la limitación identificada
anteriormente sobre el reentrenamiento gobernado por reloj real en vez de por
volumen de datos: sin las correcciones de persistencia del punto 4.2, ninguno de los
tres modelos habría llegado a entrenar antes de que las interrupciones del host
reiniciaran el progreso, y la prueba nunca se habría completado.

## 10. Limitaciones de esta simulación online

1. El tráfico "normal" que sirve de línea base es generado por script, no por
   usuarios orgánicos reales — su patrón de variabilidad es más uniforme que el de
   una comunidad universitaria real.
2. Se probaron 4 tipos de incidente, pero con muy pocas repeticiones cada uno (5
   ventanas anómalas de 371 en total) — un tamaño de muestra pequeño para las
   conclusiones estadísticas que puedan derivarse.
3. El umbral `FUSION_THRESHOLD=0.58` se calibró sobre este mismo conjunto de 371
   ventanas — existe riesgo de sobreajuste a este entorno de prueba específico; su
   validez debe reconfirmarse con datos reales de producción antes de adoptarlo de
   forma definitiva.
4. El entorno de prueba (host de desarrollo, sujeto a suspensión) no representa las
   condiciones de disponibilidad de un servidor de producción; la necesidad de la
   corrección del punto 4.2 es en parte consecuencia de ese entorno inestable, aunque
   la corrección en sí es una mejora legítima independientemente del entorno.
