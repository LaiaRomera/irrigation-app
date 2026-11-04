# Diseño: pipeline-riego-ml

## Context

El motivo y el alcance están en `proposal.md`. Lo que condiciona el enfoque:

- **Ya existe:** `sources/agrodevices.py` (unidades y lecturas), `aggregation.py`, `scale.py` (índice de 0 a 100), `weather/locations.py` (73 ubicaciones), `weather/open_meteo.py` (`fetch_previous_runs`, columnas `day`, `lead_days`, `precipitation_mm`, `et0_mm`, `temperature_c`, `humidity_pct` y `wind_kmh`), `scripts/download_weather.py` (reanudable) y `training_table.py` (`dawn_snapshots`, `build_training_table`, sin meteorología). Los datos viven en `data/` (fuera de git), en Parquet.
- **Arquitectura acordada:** monolito modular. Dos procesos (tareas y web) que solo se comunican por la base de datos. Streamlit sin FastAPI. Pydantic en las fronteras. Identificadores en inglés y textos en español.
- **Restricciones:** todo gratuito, ordenador de Laia (16 GB de RAM y sin GPU para el modelo clásico), unas 6 semanas, cuota de Open-Meteo de 10.000 llamadas al día, Aiven con 1 GB (no caben las lecturas a 15 minutos).
- `docs/arquitectura.md` está desactualizada (habla de fine-tuning y ReportLab). Mandan las decisiones del 22 de octubre al 4 de noviembre de 2026 de `docs/decisiones.md`.

## Goals / Non-Goals

**Goals:**
- Que cada fase deje un artefacto que se pueda comprobar por sí solo (Parquet, tabla de métricas, JSON del motor, PDF), para poder parar en cualquier punto con algo entregable.
- Que el entrenamiento sea en local, a partir de archivos, y la operación (predicción, motor e informe) contra la base de datos, solo para la parcela de referencia.
- Que haya pruebas con datos sintéticos en cada módulo nuevo, al estilo de `tests/` (pytest, sin red ni base de datos salvo las pruebas de integración marcadas).

**Non-Goals:**
- Ingesta automática y en vivo de Agrodevices: en el MVP las lecturas de la parcela de referencia se cargan desde el export.
- Desplegar la tarea diaria en GitHub Actions: se ejecuta en local con un script.
- Cargar en Aiven las 128 unidades: solo la parcela de referencia.
- Ajuste fino de hiperparámetros más allá de una búsqueda pequeña y reproducible.

## Decisions

### D1. Tabla de entrenamiento: unir la meteorología como «suma hasta el horizonte», alineada con la hora de origen
El ejemplo de horizonte N predice el cambio entre las 05:00 UTC del día de origen t y las 05:00 UTC del día t+N. Esa ventana cubre 19 horas del día t, los días t+1 a t+N−1 completos y solo 5 horas del día t+N. Por eso se suman la lluvia y la ET₀ previstas de los días **t a t+N−1** (no de t+1 a t+N, como decía una versión anterior de este plan: así el horizonte 1 habría visto el tiempo de mañana y no el de hoy, que es el que está cayendo sobre el suelo). Cada día t+k se toma con la previsión de antelación k (`lead_days = k`, de 0 a 6). Se toma también la media de la temperatura, la humedad del aire y el viento de esos días. Así el número de columnas es fijo para todos los horizontes y el modelo de cada horizonte ve lo que tendrá en producción.
- Open-Meteo documenta `previous_day0` como la ejecución actual de la previsión, así que existe y se pide junto a las demás antelaciones (siete antelaciones por cinco variables son 35 columnas: el mismo coste que de 1 a 7).
- *Limitación declarada:* la antelación 0 del archivo es el modelo más reciente para cada hora, algo más exacta que la previsión de las 05:00 para las horas de la tarde. Es un pequeño sesgo optimista y se menciona en las limitaciones.
- Las mismas funciones (`features.py`) construyen las variables al entrenar y al predecir, para que producción no calcule algo distinto de lo que vio el modelo.
- *Alternativa:* una columna por día y variable (35 columnas con nulos en los horizontes cortos). Se descarta porque hace la tabla dispersa y complica Ridge.
- La unión se hace por `unit_id → location_id` (`unit_locations.parquet`) y por día. Va en un módulo nuevo `irrigation_app/features.py`, y `training_table.py` sigue siendo independiente de la meteorología.

### D2. Regla de riegos: subida diaria del índice de las 05:00 sin lluvia
Se marca un riego si `índice(d) − índice(d−1) > umbral_subida` y la lluvia del intervalo entre las dos madrugadas es menor que `lluvia_mínima`. Ese intervalo cubre 19 horas del día d−1 y 5 del día d, así que se aproxima con la lluvia del día d−1 con antelación 0 (la previsión de ese mismo día), no la del día d. El riego se marca **por unidad**. La subida se calcula con la media de las profundidades o con «cualquier profundidad» (parámetro): la media diluye un riego que solo moja las capas superiores y «cualquier profundidad» marca más falsos positivos; la tarea 2.5 mide las dos. Los valores iniciales (por ejemplo, 2 puntos y 2 mm) se ajustan mirando la 1168 y un par de unidades más, y quedan registrados en la salida.
- *Medido el 4 de noviembre de 2026* con la media de profundidades y sin mirar la lluvia (caso más duro), con umbral de 2 puntos: se marca el 5,8 % de los días de las 128 unidades (el 10 % en la 1168) y sobreviven el 94 % de los ejemplos a 1 día, el 85 % a 3, el 78 % a 5 y el 72 % a 7. Con umbral de 5 puntos, el 98 %, 94 %, 92 % y 89 %. La exclusión no deja el conjunto vacío, pero se concentra en las unidades que riegan a diario.
- *Alternativa:* usar el reanálisis para la lluvia observada. Es mejor dato, pero exige otra descarga (más cuota). Queda como mejora si la regla da demasiados falsos positivos.
- Módulo nuevo `irrigation_app/irrigation_marks.py`. La exclusión se aplica en la construcción final de la tabla.

### D3. Validación: un único reparto fijo y guardado
Se fija una fecha de corte (propuesta: la que deja alrededor del 20 % de los ejemplos limpios después) y se aparta alrededor del 20 % de las ubicaciones, elegidas con una semilla fija entre las que tienen datos a los dos lados del corte. El reparto se guarda en `data/ml/split.json` para que todos los métodos usen exactamente los mismos ejemplos. Hay tres conjuntos: entrenamiento (ubicaciones de entrenamiento, antes del corte), validación para elegir hiperparámetros (ubicaciones de entrenamiento, después del corte + 7 días) y test final (ubicaciones apartadas, después del corte + 7 días).
- La ubicación `41.66_0.56` agrupa 11 unidades con la coordenada que parece ser un valor por defecto de la empresa, no la de cada finca. **No se trata como una ubicación real:** sus unidades van siempre a entrenamiento y nunca se aparta como bloque, porque no son un solo sitio y un único sorteo decidiría el 9 % de las unidades. Además se repite la evaluación sin las unidades con coordenada `shared` (decidido el 3 de noviembre de 2026).
- *Alternativa:* validación cruzada temporal con varias particiones. Es más robusta, pero cuesta más tiempo de cálculo y de explicación, así que queda como mejora.

### D4. Un modelo por horizonte; Ridge con estandarización
Siete modelos por algoritmo (horizontes 1 a 7). Encaja con `ml_model.horizon_days` y con la regla de un único modelo activo por horizonte. Ridge va en un `Pipeline` con imputación de nulos e indicador de falta, más `StandardScaler`. LightGBM admite los nulos directamente. Los archivos se guardan con `joblib` en `data/ml/models/` (fuera de git), y la ruta queda en `artifact_path`.
- *Alternativa:* un solo modelo con el horizonte como variable. Se descarta porque la meteorología acumulada cambia de sentido con el horizonte y la tabla `ml_model` ya se diseñó por horizonte.

### D5. Esquema: `ml_model` y `ml_prediction` en `create_schema.sql`
Llevan las columnas acordadas el 3 de noviembre de 2026, un índice único parcial `(horizon_days) WHERE is_active` y un disparador de inmutabilidad en `ml_prediction` (como el de `recommendation`). **`scripts/apply_schema.py` se niega a actuar sobre una base que ya tiene tablas** (no pisa nada), y tanto el Docker local como Aiven ya tienen las 12 actuales. Por eso las dos tablas van en un archivo de migración, `sql/migrations/001_ml_tables.sql`, que el mismo script aplica con una opción nueva `--migration <archivo>` (mismas salvaguardas: solo la base `irrigation_app` y se niega si alguna tabla del archivo ya existe). `sql/create_schema.sql` también se actualiza, para que una base nueva nazca completa. Se aplican primero en el Docker local y después en Aiven.

### D6. Parcela de referencia como configuración versionada y sin datos sensibles
`src/irrigation_app/reference_plot.py` (en git, sin datos de la empresa más allá del identificador de la unidad) es la configuración, escrita como código Python por preferencia de Laia: un modelo Pydantic y una instancia que se valida al importar, sin librerías de configuración ni archivos que leer. Recoge la textura, el cultivo, el tipo de riego, la fecha de inicio del ciclo, la superficie, el origen de cada dato, los días de instalación excluidos y `status: "candidate" | "confirmed"`. Las tablas FAO-56 (textura → CC, PMP y TAW; cultivo → Zr, p y Kc por etapa; riego → Ea) van en `irrigation_engine/reference_values.py` con la fuente de cada cifra. Los anclajes se calculan con un script a partir de `daily.parquet` (sin los días de instalación) y el script los muestra con su método y periodo; tras revisarlos con Laia se copian a la configuración (son una decisión y quedan en git): la base de datos no tiene columnas para ellos, así que el servicio de informes los lee de ahí y los copia en `recommendation.inputs_snapshot`. Un script idempotente carga la parcela, el usuario de prueba (`app_user`) y los sensores en la base de datos, y las **lecturas de 15 minutos** de la unidad en `sensor_reading` (`value` = índice de 0 a 100, `is_interpolated` según el tipo `SMMi`, `quality_flag = 'ok'`): el esquema pide guardarlas sin resumir y las unas 230.000 filas de la 1168 caben en Aiven. La humedad de las 05:00 se calcula desde ellas, igual que al entrenar.
- *Alternativa:* poner las tablas agronómicas en la base de datos. Se descarta para el MVP porque un archivo es más fácil de revisar y citar.

### D7. Motor `irrigation_engine/`
Paquete con tres partes: `inputs.py` (modelos Pydantic de entrada: parcela, predicciones por sensor y día, y meteorología), `calculation.py` (funciones puras: zona radicular, agotamiento, primer cruce de p, dosis, volumen y sensibilidad) y `result.py` (salida: plan de 7 días, decisión por día, estado de validación, factores y limitaciones). La sensibilidad recalcula la dosis con los anclajes desplazados ±10 % de su rango. La etapa y el Kc salen de un calendario genérico por cultivo y de `cycle_start_date`, y se marcan como estimación en `limitations`. El nombre `irrigation_engine` es el ejemplo de `decisiones.md` y **se adopta en este cambio**.

### D8. Orquestación de la operación: `generate_report(plot_id, user_id, report_type)`
Una función del paquete (`irrigation_app/reports/service.py`) que comprueba el permiso, lee de la base de datos las últimas predicciones y la meteorología de la parcela, ejecuta el motor, guarda la recomendación (versión nueva), genera el texto (LLM o plantilla), lo valida, guarda el informe y devuelve su identificador. La web solo llama a esta función. El tipo `daily` detalla el primer día del plan y `weekly` es ese mismo informe más los otros seis días en forma resumida (decisión, dosis en mm si la hay y lluvia prevista; `report.report_type` ya admite los dos). Las predicciones las escribe aparte `scripts/run_daily.py` (en local, a las 05:00 o a mano): carga las lecturas nuevas de la parcela de referencia, obtiene la previsión de Open-Meteo (ver D12) y escribe en `ml_prediction`. **La opción `--as-of AAAA-MM-DD` reproduce un día pasado** con las previsiones archivadas de ese día: el export de lecturas termina el 2 de noviembre y, como a los 3 días sin lecturas todo pasa a `insufficient_data`, sin ella la demostración de la defensa (mediados de diciembre) no daría ninguna dosis.

### D9. LLM detrás de una interfaz y con salida JSON
`reports/llm.py` define una interfaz `TextGenerator` con dos implementaciones: `LlmGenerator` (cliente HTTP del proveedor, con tiempo de espera y salida JSON con `summary`, `reason` y `warning`) y `TemplateGenerator`. El proveedor se elige con variables de entorno (`LLM_PROVIDER`, `LLM_MODEL` y la clave en `.env`), así que se puede elegir más tarde sin tocar las especificaciones. El prompt vive en un archivo versionado (`reports/prompts/agronomist_v1.md`). La validación de cifras (`reports/validation.py`) extrae los números con su unidad del texto (con coma o punto decimal) y los compara con el conjunto de cifras permitidas que sale del resultado del motor, con una tolerancia de redondeo de una cifra decimal.

### D10. PDF: Jinja2 sobre la plantilla de Agrodevices y Playwright
La plantilla se lee de `AGRODEVICES_TEMPLATE_DIR` (ruta local, fuera de git). El código rellena un contexto con los campos de `01_Documentacion_variables_informe` usando Jinja2, las gráficas se generan con Matplotlib como SVG o PNG insertado, y Playwright (Chromium sin interfaz) imprime a PDF en memoria. Para las pruebas se usa una plantilla mínima sintética en `tests/fixtures/`. Playwright se prueba el primer día de la fase (condición de la decisión del 4 de noviembre de 2026).

### D11. Web Streamlit
Va en `irrigation_app/web/`, con `app.py` como punto de entrada. Login con `st.login` (Auth0), y el email se cruza con `app_user` (la tabla se llama así porque `user` es una palabra reservada de PostgreSQL). Páginas: resumen de parcelas, detalle e informe. Todas las consultas pasan por `irrigation_app/access/`, que filtra por `user_id`. El PDF se descarga con `st.download_button`, generado al pedirlo.

### D12. Meteorología operativa: la previsión de hoy hace de antelación 0, 1, 2…
En producción no hay Previous Runs: lo que existe es la previsión actual. El conector necesita una segunda función, `fetch_forecast`, que la pide y la deja en `forecast_data` (una fila por parcela, día, antelación y origen, con los nombres de columna de esa tabla: `rainmm`, `eto`, `tmedia`, `rhmedia`, `vientomedia`; `lead_days` desde 0). `features.py` lee de ahí las mismas variables que en el entrenamiento salieron de Previous Runs. Con `--as-of` se llena la misma tabla con las previsiones archivadas de un día pasado (`source` las distingue). Como la ET₀, la lluvia y las medias de la previsión actual y de Previous Runs proceden del mismo modelo, se comprueba que salen del mismo orden de magnitud para un mismo día.

## Risks / Trade-offs

- [Cuota u error 429 de Open-Meteo que retrasa la meteorología] → El script es reanudable. Mientras tanto se puede avanzar con el motor, el informe y la web usando datos sintéticos. Si hace falta, primero las ubicaciones con más ejemplos.
- [La regla de riegos deja pocos ejemplos limpios en los horizontes largos (riegos de verano muy frecuentes)] → Medido: con umbral de 2 puntos sobreviven el 72 % de los ejemplos a 7 días en el conjunto, pero las unidades que riegan a diario pierden mucho más (la 1168 marca el 10 % de sus días). Se muestra el recuento por horizonte y por unidad (especificación de `training-dataset`). Si un horizonte queda con pocos ejemplos, sus predicciones se marcan `provisional` y se documenta.
- [Los días de instalación afectan a casi todas las unidades, no solo a la 1168] → Una regla automática los marca para las 128 (98 tienen valores menores de 20 en sus dos primeras semanas) y una lista manual permite corregirla.
- [Las lecturas se quedan viejas antes de la defensa: el export termina el 2 de noviembre] → `run_daily.py --as-of` reproduce un día pasado y se pide a Agrodevices un export reciente de la parcela de referencia (tarea 0.1).
- [Las coordenadas `shared`, sobre todo `41.66_0.56`, pueden dar una meteorología de otro sitio] → Esa ubicación no se aparta en la validación y se repite la evaluación sin las unidades `shared`.
- [Ridge y LightGBM no ganan a la persistencia] → Es un resultado válido del TFM. Se documenta y el sistema funciona igual con la persistencia como modelo activo.
- [No se consiguen el cultivo y el suelo de la 1168] → La configuración admite `status: candidate` y otra unidad. El motor da recomendación cualitativa si falta un parámetro.
- [Los anclajes son inciertos con la escala relativa] → La recomendación se marca indicativa y lleva el análisis de sensibilidad.
- [Playwright o Chromium no funciona en Streamlit Community Cloud] → Para la defensa, todo en local. Plan B: generar el PDF en local y servir el HTML en la nube.
- [Coste o capa gratuita del LLM] → Plantilla de respaldo siempre disponible. Proveedor intercambiable por configuración.
- [La plantilla de Agrodevices es un documento interno en un repositorio público] → Se lee desde una ruta local. En git solo va la plantilla sintética de pruebas.
- [Alcance muy grande para 6 semanas] → Las tareas están por fases con un entregable cada una y un plan semanal orientativo al final de `tasks.md`. Si el plazo aprieta, se recortan por este orden: las alertas, el informe semanal, aplicar en Aiven, el pulido de la web y el informe real con LLM (queda la plantilla). No se recorta el PDF de la parcela de referencia.

## Migration Plan

1. Aplicar la migración `sql/migrations/001_ml_tables.sql` (`ml_model` y `ml_prediction`) en el Docker local con `scripts/apply_schema.py --migration`, pasar las pruebas de restricciones y después aplicarla en Aiven.
2. Cargar la parcela de referencia y sus lecturas de 15 minutos (script idempotente).
3. Vuelta atrás: las tablas nuevas no tienen dependencias de las existentes salvo claves foráneas a `sensor`, así que se pueden borrar sin afectar al resto.

## Open Questions

- Proveedor y modelo del LLM (y si su capa gratuita basta). No cambia las especificaciones gracias a D9.
- Valores finales del umbral de subida y de la lluvia mínima de la regla de riegos. Son parámetros y se ajustan con los datos.
- Fecha de corte y ubicaciones apartadas concretas. Se fijan con los recuentos tras la exclusión.
- Si la plantilla de Agrodevices se llega a incluir en el repositorio.
- Umbrales de las alertas agronómicas («lluvia fuerte prevista» y «suelo muy seco»), que `decisiones.md` deja pendientes, y si entran o se recortan.
- Si el informe semanal entra con la misma maquetación que el diario (en este plan, el mismo informe con el plan de los siete días).
