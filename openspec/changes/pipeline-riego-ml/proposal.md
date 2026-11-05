# Propuesta: pipeline-riego-ml

## Why

Ya están listos el esquema de la base de datos, la limpieza de Agrodevices (128 unidades, 9,5 M de lecturas, índice de humedad común de 0 a 100), el conector de Open-Meteo y una primera tabla de entrenamiento (648.148 ejemplos tras quitar los días de instalación de las sondas). Pero todavía no hay ningún resultado de punta a punta: falta la meteorología en la tabla, los riegos siguen dentro de lo que se predice y no existen ni el modelo, ni el motor, ni el informe, ni la web. Quedan unas 6 semanas de TFM y el entregable del MVP es una recomendación de riego en PDF, con gráficas y explicaciones en lenguaje natural. Este cambio cubre todo el camino que falta: de los datos limpios a ese PDF.

## What Changes

1. **Meteorología en la tabla de entrenamiento.** Se completa la descarga de las previsiones archivadas (73 ubicaciones, reanudable; el 4 de noviembre dio 429) y se une a cada ejemplo del horizonte N con la meteorología de los días de origen a origen+N−1 (los que cubre el periodo a predecir desde las 05:00), cada día con su antelación (0 para el de origen, 1 para el siguiente…), sin usar el reanálisis como previsión. **Antes de descargar hay que cambiar las antelaciones del conector de 1 a 7 a 0 a 6:** la versión anterior de este plan dejaba fuera el tiempo del propio día de origen.
2. **Marcado y exclusión de riegos.** Una regla marca los posibles riegos (subida del índice sin lluvia prevista). Se excluye un ejemplo cuando su periodo a predecir contiene un riego marcado, horizonte a horizonte. Los riegos anteriores al origen no se excluyen. Se excluyen además los días de instalación de cada sonda, con una regla automática para las 128 unidades (98 tienen valores menores de 20 en sus dos primeras semanas) y una lista manual (por ejemplo, 1168 del 9 al 11 de marzo de 2025).
3. **Variables de estación y modelado.** Se añade la estación del año (día del año en forma cíclica) y la meteorología acumulada hasta el horizonte, y se fija el conjunto de variables.
4. **Entrenamiento y evaluación.** Se entrenan la persistencia (referencia), Ridge y LightGBM con un flujo reproducible. La validación aparta ubicaciones y fechas posteriores a un corte, con un margen de 7 días. Se miden MAE, RMSE y sesgo por horizonte (d1 a d7) frente a la persistencia. Se registran los modelos (`ml_model`) y las predicciones (`ml_prediction`) con la degradación por escalones ya acordada.
5. **Parcela de referencia.** Se configuran los parámetros de la parcela (textura y cultivo obligatorios; tipo de riego, fecha de inicio del ciclo y, si se conoce, la superficie, opcionales: si faltan, el motor usa un supuesto declarado) y sus anclajes (capacidad de campo y límite inferior) a partir de los datos. La candidata es la unidad 1168, **pero no se confirma hasta que Laia sepa si puede conseguir su cultivo y su suelo**. Si no se consiguen, se elige otra (5271 o 1172).
6. **Motor de riego FAO-56.** Paquete en Python puro, sin red ni base de datos, con entradas y salidas en Pydantic. Combina las profundidades dentro de la zona radicular, busca el primer día en que se cruza el umbral, calcula las dosis neta y bruta en mm (y en m³ si se conoce la superficie) y devuelve un plan de 7 días con decisión y estado de validación. La recomendación es **indicativa** con la escala relativa.
7. **Informe.** Un LLM externo por API, con rol de agrónomo, devuelve una salida estructurada (resumen, motivo, aviso). Un código comprueba que cifras y unidades coinciden con las del motor. Si no coinciden, se usa la plantilla de respaldo. El código rellena la plantilla HTML de Agrodevices y el PDF se genera con Chromium (Playwright). La recomendación queda inmutable y versionada en `recommendation` y `report`.
8. **Interfaz Streamlit.** Login (Auth0 con `st.login`, cruzado con `app_user`), resumen de parcelas, detalle, botón «solicitar informe de hoy» que llama a `generate_report(plot_id, user_id, report_type)`, vista del informe con su fecha y descarga del PDF. Los permisos se comprueban en el servidor.
9. **Predicción diaria en local, con reproducción de un día pasado.** `scripts/run_daily.py` carga las lecturas, obtiene la previsión (nueva función `fetch_forecast` en el conector, guardada en `forecast_data`) y escribe `ml_prediction`. Su opción `--as-of` reproduce un día pasado con las previsiones archivadas, porque el export de lecturas termina el 2 de noviembre y a los 3 días sin lecturas todo pasa a `insufficient_data`.
10. **Alertas mínimas e informe semanal, recortables.** `decisiones.md` mantiene las alertas agronómicas («lluvia fuerte prevista» y «suelo muy seco») y los informes diario y semanal en el MVP, así que se incluyen en versión mínima. Son lo primero que se recorta si el plazo aprieta: **las alertas se deciden al final de la semana 4** (2 de diciembre) según el tiempo que quede. El informe semanal es el diario detallado del primer día más los otros seis días resumidos (decisión, dosis en mm si la hay y lluvia prevista).
11. **Base de datos.** Una migración (`sql/migrations/001_ml_tables.sql`) aplicable con `scripts/apply_schema.py --migration`, porque ese script se niega a actuar sobre una base que ya tiene tablas, y un módulo de conexión `db.py` dentro del paquete.

Fuera de este cambio: RAG (opcional, se decide después comparando), corrección del sesgo meteorológico, XMS-CAT (en espera), detección de anomalías, el historial de alertas y la tarea diaria programada en la nube (para la defensa se ejecuta en local).

## Capabilities

### New Capabilities
- `training-dataset`: construcción de la tabla de entrenamiento del modelo de humedad: humedad de las 05:00 UTC, unión con la meteorología prevista según la antelación, marcado y exclusión de riegos, y variables de estación.
- `moisture-forecast`: entrenamiento, validación, registro e inferencia del modelo de humedad (persistencia, Ridge, LightGBM), con métricas por horizonte y degradación por escalones.
- `reference-plot`: parámetros agronómicos de la parcela de referencia y anclajes de su escala de humedad.
- `irrigation-engine`: motor determinista FAO-56 que, a partir de las predicciones y de los parámetros de la parcela, produce el plan de riego de 7 días.
- `irrigation-report`: redacción del informe con LLM, validación de cifras, plantilla de respaldo, plantilla HTML de Agrodevices y exportación a PDF.
- `web-interface`: interfaz Streamlit con login, parcelas permitidas, petición del informe y descarga del PDF.

### Modified Capabilities
<!-- No hay especificaciones previas en openspec/specs/. -->

## Impact

- **Código nuevo** en `src/irrigation_app/`: ampliación de `training_table.py`, más `db.py`, `irrigation_marks`, `features`, `models/`, el motor (carpeta con nombre en inglés por acordar, por ejemplo `irrigation_engine/`), `reports/` y `web/`. Scripts nuevos en `scripts/` (unir la meteorología, entrenar, evaluar y predecir).
- **Base de datos:** nuevas tablas `ml_model` y `ml_prediction` (columnas ya decididas el 3 de noviembre de 2026) en `sql/migrations/001_ml_tables.sql` y en `sql/create_schema.sql`, más la carga de la parcela de referencia, de sus sensores y de sus lecturas de 15 minutos.
- **Dependencias nuevas:** `scikit-learn`, `lightgbm`, `pydantic`, `playwright` (más su Chromium), `jinja2` (para la plantilla, ya instalado por Streamlit), `matplotlib` (las gráficas del informe, que la versión anterior olvidaba) y el SDK del proveedor del LLM (proveedor y coste **pendientes**, se eligen en la fase 0). Se comprobó el 4 de noviembre de 2026 que todos tienen rueda para Python 3.14 en Windows (scikit-learn 1.9.1, lightgbm 4.7.0, pydantic 2.14.2, playwright 1.64.1 y matplotlib 3.11.2). La configuración de la parcela es un módulo Python (sin YAML ni TOML), por preferencia de Laia.
- **Datos externos:** cuota de Open-Meteo (unas 6.500 llamadas, puede llevar más de un día) y la API del LLM (coste por informe).
- **Documentos internos:** la plantilla de Agrodevices **no** se sube al repositorio público mientras no se decida lo contrario. Se lee desde una ruta local configurable.
- **Documentación:** `docs/decisiones.md`, `docs/arquitectura.md` (desactualizada respecto al 22 de octubre de 2026) y `docs/base_de_datos.md`.
