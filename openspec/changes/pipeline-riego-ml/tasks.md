# Tareas: pipeline-riego-ml

**Quién hace cada fase, cómo se coordinan y qué contratos hay entre ellos:** `openspec/agents.md`. Las notas de traspaso entre agentes van en `handoffs.md`, junto a este archivo.

**Reglas para quien ejecute estas tareas** (recogidas de cómo trabaja Laia):
- Un paso pequeño cada vez, comprobado antes de seguir. Identificadores en inglés; comentarios, documentación e interfaz en español.
- **Las decisiones se anotan en `docs/decisiones.md` junto con Laia:** se propone el texto y se registra cuando ella lo aprueba. Donde una tarea dice «anotar», se entiende así.
- No hacer `commit` ni `push` sin que Laia lo pida. Nada de datos de la empresa en el repositorio (`data/` está ignorado entero).
- Los 45 tests actuales (`pytest`) deben seguir en verde tras cada tarea.

## 0. Comprobaciones previas y peticiones externas (antes de empezar a programar)

**Responsables:** Laia (0.1, y 0.4 con ReportUI); DataEngineerML (0.2 y 0.5); ReportUI (0.3 y 0.4).

- [ ] 0.1 Laia pide ya, en paralelo, lo que tarda en llegar. **Ya conseguido el 5 de noviembre de 2026:** el cultivo (manzano), el riego (goteo de una línea), la textura (franco arcilloso limoso) y el significado de `SMMAdmin`. **Falta:** un export reciente de las lecturas de la 1168 para la defensa (el actual termina el 2 de noviembre y a los 3 días sin lecturas todo pasa a `insufficient_data`); si la coordenada 41,656 / 0,557 es la de la empresa (solo afecta a la meteorología de 11 unidades, no a la 1168); y las preguntas sobre las líneas de saturación y de marchitez. Verificar: las respuestas, o la fecha en que se esperan, anotadas en `docs/decisiones.md`
- [ ] 0.2 Añadir a `requirements.txt` con versión fijada `scikit-learn==1.9.1`, `lightgbm==4.7.0`, `pydantic==2.14.2`, `playwright==1.64.1`, `matplotlib==3.11.2` y la versión actual de `jinja2` (ya instalado por Streamlit). Se comprobó el 4 de noviembre de 2026 con `pip install --dry-run` que todos tienen rueda para Python 3.14 en Windows. `joblib` llega con scikit-learn. **No se añade ninguna librería de configuración:** la configuración de la parcela es un módulo Python (preferencia de Laia; ver D6). Instalar y ejecutar `playwright install chromium`. Verificar: `python -c "import sklearn, lightgbm, pydantic, playwright, matplotlib, jinja2"` funciona en `.venv`
- [ ] 0.3 Probar Playwright y Chromium en el ordenador de Laia imprimiendo a PDF la plantilla de Agrodevices sin rellenar (condición de la decisión del 4 de noviembre de 2026: se prueba el primer día, no al final). Verificar: se genera un PDF con tildes y símbolos (ET₀, m³); si falla, se anota y se valora el plan B antes de gastar semanas
- [ ] 0.4 Elegir con Laia el proveedor y el modelo del LLM (coste y capa gratuita) y probar una llamada que devuelva JSON. Añadir las variables a `.env.example`. Verificar: la llamada de prueba devuelve `summary`, `reason` y `warning`; la decisión queda anotada
- [x] 0.5 Registrar el marcador `integration` en `pytest.ini` y hacer que esas pruebas se salten solas si no hay PostgreSQL local (Docker `irrigation-pg`, puerto 5433). Verificar: `pytest -m "not integration"` ejecuta los 45 actuales en verde

## 1. Meteorología descargada y unida a la tabla de entrenamiento

**Responsable:** DataEngineerML.

- [ ] 1.1 **Antes de descargar:** alinear la ventana meteorológica con el origen (D1). Cambiar en `weather/open_meteo.py` `ALL_LEAD_DAYS` a las antelaciones **0 a 6** (hoy son 1 a 7), reescribir el comentario que lo explica, la prueba `test_by_default_it_asks_for_every_lead_time...`, el README del módulo y las líneas de `docs/decisiones.md` que dicen «de 1 a 7». Cuando pase el 429, comprobar con una petición pequeña que `precipitation_previous_day0` y `et0_fao_evapotranspiration_previous_day0` vienen completos desde junio de 2024 (Open-Meteo documenta `previous_day0` como la ejecución actual). Verificar: `pytest` en verde y la petición de prueba con `lead_days=(0,)` devuelve días completos
- [x] 1.2 Completar la descarga con `scripts/download_weather.py` (reanudar tras el 429; son unas 6.500 llamadas con los días propios de cada ubicación, y las antelaciones 0 a 6 cuestan lo mismo que 1 a 7). Verificar: el script informa de «73 de 73 ubicaciones descargadas» y `data/silver/weather_previous_runs.parquet` (antes `data/weather/`) tiene `lead_days` de 0 a 6
- [x] 1.3 Crear `src/irrigation_app/features.py` con la unión de la meteorología por `unit_id → location_id` (`data/weather/unit_locations.parquet`) y día: para el horizonte N, suma de la lluvia y la ET₀ de los días origen+0 a origen+N−1, cada día k con `lead_days = k`, y media de temperatura, humedad y viento de esos días (D1). Si falta un dato, el ejemplo queda «meteorología incompleta» sin rellenar. **Las mismas funciones las usará la inferencia** (tarea 4.4), para que entrenamiento y producción calculen las variables igual. Verificar: `tests/test_features.py` con datos sintéticos (origen lunes y horizonte 3 → lunes con antelación 0, martes con 1 y miércoles con 2; ubicación compartida; valor ausente sin rellenar)
- [x] 1.4 Ampliar `scripts/prepare_training_table.py` para que escriba la tabla con la meteorología. Verificar: el script se ejecuta, la tabla nueva tiene las columnas meteorológicas y se imprime el recuento de ejemplos con meteorología incompleta por horizonte
- [x] 1.5 Proponer a Laia la anotación en `docs/decisiones.md` de la ventana de meteorología y las cifras resultantes. Verificar: la sección del 4 de noviembre deja de decir «Falta: la meteorología prevista»

## 2. Marcado y exclusión de riegos, instalación y variables de estación

**Responsable:** DataEngineerML. **Apoya:** AgronomoFAO56 (revisa que los parámetros de la regla de riegos tengan sentido agronómico).

- [x] 2.1 Crear `src/irrigation_app/irrigation_marks.py` con la regla de posible riego por unidad: la subida del índice entre dos madrugadas consecutivas supera un umbral y la lluvia de ese intervalo es baja. La lluvia del intervalo (día d−1 a las 05:00 hasta el día d a las 05:00) se aproxima con la lluvia del día d−1 con antelación 0. Umbral y lluvia mínima parametrizables, y la subida se calcula con la **media de las profundidades o con «cualquier profundidad»** (parámetro, D2). Verificar: `tests/test_irrigation_marks.py` (subida sin lluvia se marca, subida con lluvia no, las dos formas de agregar profundidades)
- [x] 2.2 Marcar los días de instalación de cada sonda con una regla automática, no solo para la 1168: el tramo inicial de una unidad mientras su índice está por debajo de 20 antes de que llegue por primera vez a un valor plausible (con el 4 de noviembre de 2026, 98 de las 128 unidades tienen valores menores de 20 en sus dos primeras semanas, 1.772 en total), más una lista manual por unidad en la configuración. Verificar: pruebas (la 1168 pierde del 9 al 11 de marzo de 2025; una unidad cuyo suelo baja de 20 en pleno verano, después de funcionar, no se marca como instalación) y recuento de días excluidos por unidad
- [x] 2.3 Añadir a la construcción de la tabla la exclusión por horizonte (riego marcado entre origen+1 y origen+N) y la exclusión de los días de instalación como origen y como objetivo. Verificar: pruebas (un riego a 5 días quita los horizontes 5 a 7 y conserva los de 1 a 4; un riego de ayer no excluye)
- [x] 2.4 Añadir las variables de estación (seno y coseno del día del año) y conservar `depth_cm`, `base_index`, `change_1d` y `change_3d`. Verificar: prueba de continuidad entre el 31 de diciembre y el 1 de enero
- [x] 2.5 Mostrar en el script el recuento por horizonte (inicial, quitados por riego, por instalación y por meteorología, y final) y ajustar el umbral y la lluvia mínima revisando la 1168 y dos unidades más, comparando la media de profundidades con «cualquier profundidad». Referencia calculada el 4 de noviembre de 2026 sin mirar la lluvia (caso más duro) y con la media de profundidades: con umbral de 2 puntos sobreviven el 94 % de los ejemplos a 1 día, el 85 % a 3, el 78 % a 5 y el 72 % a 7; la 1168 marca el 10 % de sus días. Verificar: el recuento sale por consola y los parámetros elegidos quedan guardados junto a la tabla
- [x] 2.6 Proponer a Laia la anotación de la regla, los parámetros y los recuentos en `docs/decisiones.md`. Verificar: la entrada recoge los valores y su justificación

## 3. Entrenamiento y evaluación de modelos

**Responsable:** DataEngineerML.

- [x] 3.1 Crear `src/irrigation_app/models/split.py` con el reparto por ubicación y fecha de corte, con margen de 7 días, semilla fija y guardado en `data/ml/split.json` (D3). **La ubicación `41.66_0.56` (11 unidades con la coordenada probablemente por defecto de la empresa) no cuenta como una ubicación real:** sus unidades van siempre a entrenamiento y no se apartan como bloque. Verificar: `tests/test_model_split.py` (ningún objetivo de entrenamiento después del corte, ningún origen de validación antes del corte + 7, las ubicaciones apartadas no aparecen en el entrenamiento, la ubicación de coordenada dudosa nunca se aparta)
- [x] 3.2 Crear `src/irrigation_app/models/training.py` con persistencia, Ridge (imputación, indicador de falta y estandarización) y LightGBM por horizonte, que predicen el cambio y devuelven el nivel (D4). Verificar: pruebas (la persistencia devuelve el origen; origen 70 y cambio −2,5 dan 67,5; misma semilla, mismas métricas)
- [x] 3.3 Crear `src/irrigation_app/models/evaluation.py` con MAE, RMSE y sesgo por método y horizonte sobre los mismos ejemplos, y la mejora frente a la persistencia. Verificar: prueba con valores conocidos
- [x] 3.4 Crear `scripts/train_models.py`, que entrena, evalúa en validación, elige hiperparámetros con una búsqueda pequeña, evalúa en test y guarda los modelos en `data/ml/models/` y la tabla de métricas en `data/ml/metrics.csv`. Repetir la evaluación **sin las unidades con coordenada `shared`** (decidido el 3 de noviembre de 2026) y anotar si cambia el resultado. Verificar: el script termina, la tabla tiene 3 métodos × 7 horizontes y existe la comparación con y sin `shared`
- [x] 3.5 Proponer a Laia la anotación de los resultados (tabla de métricas e interpretación frente a la persistencia) en `docs/decisiones.md` y en un apartado del TFM en `docs/deliverations/`. Verificar: las cifras de la documentación coinciden con `metrics.csv`

## 4. Base de datos, registro de modelos y predicción operativa

**Responsable:** DataEngineerML. **Apoya:** ReportUI (revisa que `db.py` y la migración le sirvan para las consultas de la web).

- [ ] 4.1 Crear `src/irrigation_app/db.py` con la conexión a PostgreSQL desde `.env`, reutilizando lo que ya resuelve `scripts/apply_schema.py` (SSL `verify-ca` y `sslrootcert` en ruta relativa a la raíz del proyecto, porque libpq no abre el certificado si la ruta lleva la «ó» de «Programación»). Hoy no existe ningún módulo de conexión dentro del paquete. Verificar: prueba de integración que se conecta al Docker local y ejecuta `SELECT 1`
- [ ] 4.2 Añadir `ml_model` y `ml_prediction` en `sql/migrations/001_ml_tables.sql` **y** en `sql/create_schema.sql` (para una base nueva), con las columnas acordadas, el índice único parcial del modelo activo y el disparador de inmutabilidad con la función `forbid_change()` que ya existe (D5). **`scripts/apply_schema.py` se niega a actuar sobre una base que ya tiene tablas**, así que se amplía con la opción `--migration <archivo>` (mismas salvaguardas: solo la base `irrigation_app`, nunca imprime la contraseña, y se niega si alguna tabla del archivo ya existe). Actualizar `docs/base_de_datos.md` y `docs/erd.md`. Verificar: la migración se aplica en el Docker local, y las pruebas de restricciones cubren un único modelo activo por horizonte y el rechazo de UPDATE y DELETE en `ml_prediction`
- [ ] 4.3 Crear `src/irrigation_app/models/registry.py` para registrar un modelo entrenado en `ml_model` y activarlo (desactivando el anterior). Verificar: prueba de integración contra el Docker local
- [ ] 4.4 Crear `src/irrigation_app/models/inference.py` con la predicción por sensor y horizonte y la degradación por escalones (`model` / `persistence_fallback`; `validated` / `provisional` / `insufficient_data`; limitaciones `stale_reading` y meteorología ausente). **Usa las funciones de `features.py`** para las variables, las mismas del entrenamiento. Umbral de frescura de la lectura: 36 horas como valor inicial, a ajustar con datos. Verificar: pruebas de los cuatro escalones con datos sintéticos y una prueba de que el vector de variables de inferencia coincide con el del entrenamiento para el mismo ejemplo
- [ ] 4.5 Aplicar la migración en Aiven con `scripts/apply_schema.py --migration`. Verificar: el script informa de que se aplicó y las tablas nuevas existen

## 5. Parcela de referencia

**Responsables:** AgronomoFAO56 (5.1, 5.2, 5.3 y 5.5); DataEngineerML (5.4, y los datos que 5.3 necesita).

- [x] 5.1 Crear `src/irrigation_app/reference_plot.py` con el modelo Pydantic `ReferencePlot` y la instancia `REFERENCE_PLOT`: la configuración de la parcela escrita como código Python (preferencia de Laia), con la unidad 1168 confirmada (`status="confirmed"`: manzano, goteo de una línea, franco arcilloso limoso), los días de instalación y huecos (`None`) para la fecha de ciclo y la superficie, con el origen de cada dato (D6). Pydantic valida al importar. Verificar: una prueba rechaza `status="confirmed"` si falta el cultivo o la textura, y otra comprueba que la instancia actual se importa sin errores
- [x] 5.2 Crear `src/irrigation_app/irrigation_engine/reference_values.py` con las tablas FAO-56 (textura → CC, PMP y TAW; cultivo → Zr, p y Kc por etapa con calendario; riego → Ea), citando la fuente de cada cifra. Verificar: pruebas que comprueban unos valores de la tabla y que un cultivo desconocido da error claro
- [x] 5.3 Crear `scripts/compute_anchors.py`, que convierte las líneas de Agrodevices de la unidad (saturación y punto de marchitez, en frecuencia) al índice con su rango y las contrasta con los percentiles de **la media de las profundidades de la zona radicular** (15, 25 y 35 cm en la 1168) calculados sobre `daily.parquet` **sin los días de instalación**. Los anclajes son globales, no uno por profundidad. Si no hay líneas de Agrodevices, los calcula de los datos con método y periodo, y recurre a la conversión genérica con `generic_lower_anchor` si no hay periodo seco. El script **muestra** los anclajes con su fuente, método y periodo, y tras revisarlos con Laia se copian a `REFERENCE_PLOT` (son una decisión y quedan en git; la base de datos no tiene columnas para ellos); el servicio de informes los copia en `inputs_snapshot`. Verificar: pruebas de la función con series sintéticas y ejecución sobre la 1168
- [ ] 5.4 Crear `scripts/load_reference_plot.py` (idempotente), que carga el usuario de prueba en `app_user`, la parcela en `plot`, un sensor por profundidad en `sensor` y las **lecturas de 15 minutos** de esa unidad en `sensor_reading` (unas 230.000 filas para la 1168; el esquema pide guardarlas sin resumir y caben en el gigabyte de Aiven): `value` = índice de 0 a 100, `is_interpolated` según el tipo `SMMi`, `quality_flag = 'ok'`. Verificar: ejecutarlo dos veces en el Docker local no duplica filas
- [ ] 5.5 Anotar en `REFERENCE_PLOT` los datos de la 1168, **confirmada como parcela de referencia el 5 de noviembre de 2026**: cultivo manzano, riego por goteo de una línea, suelo franco arcilloso limoso, con el origen de cada dato (la fecha de inicio del ciclo y la superficie siguen opcionales: si faltan, el motor usa un supuesto declarado). `status` pasa a `confirmed`. Verificar: `status` pasa a `confirmed` solo con esos datos, y queda anotado en `docs/decisiones.md`

## 6. Motor de riego FAO-56

**Responsable:** AgronomoFAO56. **Apoya:** ReportUI (revisa que el resultado del motor se pueda leer y explicar).

- [x] 6.1 Crear `src/irrigation_app/irrigation_engine/inputs.py` y `result.py` con los modelos Pydantic de entrada y salida (D7). Verificar: pruebas de validación (una eficiencia de 1,4 se rechaza nombrando el campo)
- [x] 6.2 Implementar en `calculation.py` la zona radicular, el agotamiento con anclajes, el primer cruce de p, las dosis neta y bruta en mm, el volumen solo con superficie, la etapa y el Kc estimados, y la decisión por día (`irrigate`, `postpone`, `do_not_irrigate`, `insufficient_data`, los valores que admite `recommendation.decision`). La ET₀ llega de Open-Meteo y **no se calcula**: el motor solo aplica `ETc = Kc × ET₀`. Verificar: pruebas de los escenarios de la especificación (sonda de 55 cm fuera de la zona, cruce el día 4, sin superficie, sin cruce, falla una sonda → `provisional`, todo `insufficient_data` → sin dosis)
- [x] 6.3 Añadir el análisis de sensibilidad (anclajes ±10 %) y la marca de indicativa con escala relativa. Verificar: prueba de que la salida trae la dosis mínima y máxima y la marca
- [x] 6.4 Prueba de determinismo (mismas entradas, mismo resultado serializado) y de que el paquete no importa red ni base de datos. Verificar: `pytest tests/test_irrigation_engine*.py` en verde
- [ ] 6.5 Guardar la recomendación en `recommendation` (versión nueva enlazada, copia de entradas con los anclajes y el origen de cada factor) desde `src/irrigation_app/storage/`, usando `db.py`. Verificar: prueba de integración (un recálculo crea fila nueva y no cambia la anterior)
- [x] 6.6 Documentar el motor (fórmulas, supuestos y limitaciones) en `docs/arquitectura.md` (sustituyendo la parte desactualizada) y proponer la anotación en `docs/decisiones.md`. Verificar: el documento nombra `irrigation_engine/` y las fórmulas coinciden con el código

## 7. Informe con LLM y PDF

**Responsable:** ReportUI. **Apoya:** AgronomoFAO56 (revisa el prompt y los textos de la plantilla de respaldo) y DataEngineerML (revisa las consultas a la base de datos).

- [ ] 7.1 Crear `src/irrigation_app/reports/validation.py` con la extracción de cifras y unidades (coma o punto) y la comparación con las permitidas por el motor. Verificar: pruebas (12,4 frente a 12 falla, una cifra inventada falla, las cifras correctas pasan)
- [ ] 7.2 Crear `reports/llm.py` con `TextGenerator`, `TemplateGenerator` y `LlmGenerator` (proveedor por variables de entorno ya elegido en 0.4, tiempo de espera, salida JSON con `summary`, `reason` y `warning`) y el prompt versionado `reports/prompts/agronomist_v1.md` (D9). Verificar: pruebas con un cliente simulado (respuesta correcta → `llm`; sin estructura, sin clave o con error → `template`)
- [ ] 7.3 Crear `reports/pdf.py` con el rellenado Jinja2 de la plantilla (ruta `AGRODEVICES_TEMPLATE_DIR`), las gráficas con Matplotlib y la impresión con Playwright (D10). El informe incluye el **error típico por horizonte** del backtesting, que sale de `ml_model.metrics`. Plantilla sintética en `tests/fixtures/`. Verificar: prueba que genera un PDF no vacío a partir de la plantilla sintética y muestra la marca de indicativa o de `insufficient_data` y el error típico
- [ ] 7.4 Crear `reports/service.py` con `generate_report(plot_id, user_id, report_type)`: permiso, motor, recomendación, texto, validación y guardado en `report`. El tipo `daily` detalla el primer día del plan y `weekly` es ese mismo informe más los otros seis días en forma resumida: decisión, dosis en mm si la hay y lluvia prevista (D8). Verificar: prueba de integración contra el Docker local, incluido el rechazo de una parcela ajena y dos informes (LLM y plantilla) de la misma recomendación
- [ ] 7.5 Generar un informe real de la parcela de referencia con el proveedor elegido en 0.4. Verificar: pasa la validación o recurre a la plantilla con el motivo registrado, y queda anotado el coste por informe

## 8. Predicción diaria en local y alertas

**Responsable:** DataEngineerML. **Apoya:** AgronomoFAO56 (umbrales de las alertas).

- [ ] 8.1 Añadir al conector `fetch_forecast(latitude, longitude, days)` para la previsión actual (en producción no hay Previous Runs: la previsión de hoy es la antelación 0 para hoy, 1 para mañana, etc.) y la función que la guarda en `forecast_data` con el nombre de las columnas de esa tabla (`rainmm`, `eto`, `tmedia`, `rhmedia`, `vientomedia`; `source` distingue la previsión actual de Previous Runs y `lead_days` va de 0 en adelante). Verificar: pruebas sin red; y que, para el mismo día de origen, las variables que calcula `features.py` desde la previsión actual y desde Previous Runs son del mismo orden de magnitud
- [ ] 8.2 Crear `scripts/run_daily.py` con la opción `--as-of AAAA-MM-DD` (por defecto, hoy), que carga las lecturas nuevas de la parcela de referencia, obtiene la previsión (la actual, o las previsiones archivadas del día `--as-of` para **reproducir un día pasado**), escribe las predicciones en `ml_prediction` con el modelo activo y registra la ejecución en `job_run`. Con `--as-of` se puede enseñar en la defensa una recomendación aunque el export de lecturas sea antiguo. Verificar: tras ejecutarlo hay 7 predicciones por sensor para el origen del día, y repetirlo no duplica filas
- [ ] 8.3 Documentar cómo ejecutarlo y reproducir un día en `docs/puesta_en_marcha.md`. Verificar: el comando documentado funciona tal cual
- [ ] 8.4 **Se decide al final de la semana 4 (2 de diciembre) si entran o no, según el tiempo que quede; hasta entonces no se empieza.** **Alertas agronómicas mínimas (recortable si el plazo aprieta):** en `run_daily.py`, generar en `alert` las de «lluvia fuerte prevista» y «suelo muy seco» (umbrales por acordar con Laia) sin duplicar una activa del mismo tipo, y resolverlas cuando dejen de cumplirse. Verificar: pruebas de creación, no duplicación y resolución con datos sintéticos

## 9. Interfaz Streamlit

**Responsable:** ReportUI.

- [ ] 9.1 Crear `src/irrigation_app/access/` con las consultas filtradas por usuario (parcelas, último informe y datos usados). Verificar: pruebas de integración (un usuario no ve parcelas ajenas; un email desconocido o inactivo en `app_user` no tiene acceso)
- [ ] 9.2 Crear `src/irrigation_app/web/app.py` con `st.login`, cruce del email con `app_user` y las páginas de resumen y detalle, solo de lectura (D11), y las alertas de la parcela si 8.4 está hecha. Verificar: probado en local: al abrir no se llama a Open-Meteo ni al LLM (comprobado con registros o simulación) y se ven las fechas del último informe
- [ ] 9.3 Añadir el botón «solicitar informe de hoy» que llama a `generate_report`, la vista del informe con su fecha y la descarga del PDF, más el aviso de informe antiguo y los errores sin detalles internos. Verificar: probado en local con la parcela de referencia (informe nuevo, informe antiguo con fecha visible y error simulado con aviso genérico)
- [ ] 9.4 Documentar el arranque de la web en `docs/puesta_en_marcha.md` y las pantallas en `docs/arquitectura.md`. Verificar: el comando documentado arranca la app

## 10. Integración de punta a punta

**Responsable:** ReportUI lidera; los tres participan y cada uno comprueba su tramo.

- [ ] 10.1 Recorrido completo en local: `run_daily.py` → web → solicitar informe → descargar PDF de la parcela de referencia. Verificar: PDF con la dosis (o la explicación de por qué no la hay), las gráficas, la fecha y el generador, y `pytest` completo en verde
- [ ] 10.2 Validar el cambio con `npx @fission-ai/openspec@latest validate pipeline-riego-ml --strict`. Verificar: sin errores

## 11. Memoria del TFM y defensa

**Responsables:** cada agente redacta la parte de lo que ha construido; ReportUI prepara la demostración; Laia aprueba todo lo que se escribe.

- [ ] 11.1 Redactar las entregas del curso que cuelgan de este trabajo (datos y preparación, modelo y resultados, motor y recomendación, informe e interfaz) con las cifras de `docs/decisiones.md` y las limitaciones declaradas (escala relativa, anclajes, una parcela de referencia, pocos ciclos por sonda). Las fechas de entrega las confirma Laia. Verificar: cada entrega cita el archivo de donde sale cada cifra
- [ ] 11.2 Preparar el guion de la demostración (con `run_daily.py --as-of` y el PDF de la parcela de referencia). **Elegir con Laia un día de campaña de riego** (por ejemplo, de agosto o septiembre de 2026, con lecturas y previsiones): a mediados de diciembre el manzano está en reposo y cualquier recomendación real sería «no regar, fuera de campaña». **Laia pidió que se le recuerde al llegar a esta tarea** y un plan B sin red (informe de plantilla guardado). Verificar: ensayo completo en el ordenador de Laia sin conexión al LLM

## Plan semanal orientativo (quedan unas 6 semanas desde el 4 de noviembre de 2026)

| Semana | Fases |
|---|---|
| 1 (5 a 11 de noviembre) | 0, 1 y 2 |
| 2 (12 a 18) | 3 y 4 |
| 3 (19 a 25) | 5 y 6 |
| 4 (26 de noviembre a 2 de diciembre) | 7 y 8 |
| 5 (3 a 9 de diciembre) | 9, 10 y memoria (11.1) |
| 6 (10 a 15 de diciembre) | Margen, defensa (11.2) y ajustes |

**Qué se recorta primero si hay retraso, por orden:** las alertas (8.4), el informe semanal (7.4), aplicar en Aiven (4.5), el pulido de la web (9.4) y el informe real con LLM (7.5, queda la plantilla). **No se recorta:** meteorología con la ventana correcta, modelo contra persistencia, motor, informe en PDF de la parcela de referencia.

## Workflow follow-up

- Archivar el cambio con `npx @fission-ai/openspec@latest archive pipeline-riego-ml` cuando Laia lo haya revisado.
- Comprobar que las especificaciones quedan en `openspec/specs/` tras archivar.
