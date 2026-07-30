# III.10 Limitaciones y Consideraciones

## 1. Limitaciones de la validación actual

Aunque la solución propuesta demuestra viabilidad técnica y resultados satisfactorios en simulación, la validación realizada presenta limitaciones que deben explicitarse para interpretar los hallazgos con rigor.

### 1.1 Uso de datos sintéticos
La evaluación experimental se ejecutó sobre series temporales sintéticas que reproducen patrones de uso similares a los de Moodle, pero no sustituyen por completo el comportamiento real de la plataforma de la UCI. Esto implica que factores como heterogeneidad de usuarios, campañas académicas específicas, mantenimiento del sistema, incidentes de red o picos no previstos no están totalmente representados.

### 1.2 Tamaño inicial del conjunto de evaluación
La simulación principal se realizó con 300 ventanas temporales y 15 anomalías inyectadas. Aunque este tamaño fue suficiente para verificar la operación del pipeline completo, todavía es reducido para una estimación robusta de desempeño en escenarios de producción. En particular, la tasa de falsos positivos observada en los modelos individuales sugiere la necesidad de más historial real para estabilizar los umbrales.

### 1.3 Dependencia de parámetros preliminares
Algunos hiperparámetros y umbrales fueron definidos a partir de configuración inicial y criterios empíricos:
- orden ARIMA y estacionalidad
- longitud de secuencia del LSTM
- contaminación del Isolation Forest
- pesos de la fusión
- umbrales adaptativos de alertas

Estos valores son adecuados para el prototipo, pero requieren ajuste fino con datos reales para maximizar su efectividad.

### 1.4 Validación parcial del entorno de integración
El sistema dispone de integración con Moodle, Redis, InfluxDB y Grafana; sin embargo, la validación técnica documentada mostró que, en el entorno de pruebas, los servicios externos no siempre estaban activos simultáneamente. Eso impidió evaluar de manera completa el flujo extremo a extremo en condiciones reales de operación continua.

### 1.5 Ausencia de evaluación sobre incidentes reales
No se contó con un conjunto de ataques o anomalías reales confirmadas sobre el EVA Moodle de la UCI. En consecuencia, la evaluación se centró en anomalías simuladas y patrones sintéticos equivalentes, lo que limita la extrapolación directa de los resultados hacia eventos de seguridad reales.

## 2. Consideraciones metodológicas

### 2.1 Prioridad del recall sobre la precisión
En el contexto de detección temprana de ataques, se priorizó el recall frente a la precisión. Este criterio es coherente con el objetivo del sistema: resulta más costoso no detectar un ataque que generar una alerta adicional que luego pueda ser revisada manualmente.

### 2.2 Contextualización académica
El uso del calendario académico y de umbrales adaptativos mejora la sensibilidad del sistema ante períodos de menor actividad, pero también introduce dependencia de la calidad de la información contextual. Si el calendario no está actualizado, la adaptación del umbral puede ser menos efectiva.

### 2.3 Escalabilidad operativa
La arquitectura modular facilita la expansión futura; no obstante, el rendimiento observado en simulación muestra que la escalabilidad todavía depende de la configuración de los componentes de almacenamiento y procesamiento. Esto sugiere que el sistema es apto para un escenario de prototipo o preproducción, pero requiere optimización adicional para cargas significativamente mayores.

## 3. Trabajos futuros

### 3.1 Validación con datos reales de Moodle
Como siguiente paso, el sistema debe ser conectado a una instancia real de Moodle de la UCI para recolectar métricas operativas de varias semanas. Esto permitirá:
- recalibrar umbrales dinámicos
- ajustar pesos de fusión
- estimar tasas reales de falsos positivos y falsos negativos
- validar la hipótesis en un entorno operativo auténtico

### 3.2 Optimización de hiperparámetros
Se recomienda realizar una búsqueda sistemática de parámetros sobre datos reales para:
- ajustar el orden ARIMA y la estacionalidad
- redefinir la longitud de secuencia del LSTM
- refinar la contaminación del Isolation Forest
- recalibrar los pesos de la fusión ponderada

### 3.3 Mejora del mecanismo de alertas
La gestión de alertas puede evolucionar hacia un sistema más completo que incluya:
- categorización por criticidad operativa
- integración con correo institucional o mensajería
- historial centralizado de eventos
- correlación con indicadores académicos

### 3.4 Fortalecimiento de seguridad y operación
Se propone sustituir credenciales embebidas en configuración por mecanismos más seguros, como variables de entorno o gestor de secretos, además de incorporar auditoría centralizada del acceso al dashboard y a los componentes de datos.

### 3.5 Extensión de la arquitectura analítica
La arquitectura admite incorporar nuevos modelos sin reescribir el sistema base. En trabajos futuros puede evaluarse:
- Transformers o modelos de atención
- detección por ensamblado jerárquico
- mecanismos de aprendizaje continuo más robustos
- explicabilidad de decisiones por modelo

## 3.6 Estado de la integración de módulos avanzados (post-fusión de ramas)

Este repositorio consolida `main` con las ramas `dashboard-grafana-fix` y
`feature-complete-system`, que existían sin fusionar. Como parte de esa
consolidación se conectaron al pipeline principal (`main.py`) los módulos
que en `feature-complete-system` existían como código suelto sin usar:

- **Alertas multicanal** (`alerts/channels.py`): Webhook y Slack, además del
  correo y el log ya existentes, todos gestionados por el umbral adaptativo
  de `alerts/alert_manager.py` (RF7).
- **Detección de concept drift** (`drift/concept_drift_detector.py`, Page-Hinkley
  + ADWIN): se ejecuta en cada ventana sobre el error de predicción de ARIMA,
  y publica su estado en Redis para que la API REST lo consulte.
- **Fusión adaptativa** (`models/adaptive_fusion.py`): variante opcional de
  RF6 con pesos ajustables; con los pesos iniciales usados (idénticos a los
  validados en `models/anomaly_detector.py::MODEL_WEIGHTS`) su comportamiento
  por defecto es equivalente a la fusión estática validada (93.3% recall),
  ya que el mecanismo de retroalimentación (`update()`) requiere etiquetas
  reales aún no disponibles.
- **API REST** (`api/api_server.py`): expuesta como proceso independiente
  opcional; se corrigieron la autenticación (ahora lee `config.yaml` en vez
  de solo variables de entorno) y el endpoint de concept drift (ahora lee
  el estado publicado por el proceso principal en Redis, en vez de
  instanciar un detector local que siempre estaría vacío).

**Lo que sigue pendiente**, y se deja explícitamente como trabajo futuro y no
como integrado: el modo de extracción por *CDC triggers*
(`extractors/real_time_extractor.py::CDCTriggerExtractor`). El propio script
SQL incluido (`CDC_TRIGGER_SQL`) crea el trigger de MySQL y una tabla
intermedia, pero el procedimiento almacenado que debería publicar los
cambios a Redis nunca llegó a implementarse (solo contiene un comentario
indicando dónde iría esa publicación). Completar ese puente MySQL→Redis
es condición necesaria antes de poder usar `extraction_mode: cdc_triggers`
en producción; mientras tanto, el modo `polling` (implementado en
`capture/log_capture.py`, el módulo validado y citado en el Capítulo III)
sigue siendo el mecanismo de captura por defecto y el único probado.

## 4. Conclusión de la sección

La solución propuesta cumple su propósito como prototipo funcional y validado en simulación. Sin embargo, la evidencia obtenida debe interpretarse como una validación inicial. La consolidación definitiva de la propuesta exige pruebas con datos reales, calibración fina de parámetros y evaluación en condiciones operativas sostenidas.

En consecuencia, la solución es técnicamente viable, pero su madurez final depende de la fase de validación en entorno real y de la iteración posterior sobre los hallazgos experimentales.
