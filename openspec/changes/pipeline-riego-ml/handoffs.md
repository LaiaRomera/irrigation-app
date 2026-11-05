# Notas de traspaso: pipeline-riego-ml

Aquí se hablan los tres agentes (DataEngineerML, AgronomoFAO56 y ReportUI) y se avisa a Laia. Cada nota es una cosa que otro necesita saber, esperar o revisar. **Se añade siempre al final; nunca se borra ni se reescribe lo anterior.** Las normas del equipo están en `openspec/agents.md`.

## Formato de una nota

```
### AAAA-MM-DD · De → Para · Asunto corto
- **Qué:** lo que está listo, lo que se pide o lo que cambia (con la ruta del archivo o el nombre de la tarea).
- **Contrato:** si cambia C1 a C5, cuál y cómo. «Ninguno» si no cambia nada.
- **Necesito de ti:** qué espero del otro y para cuándo. «Nada» si es solo un aviso.
- **Estado:** abierta | respondida | cerrada
```

Para Laia, usar `Para: Laia` y decir con claridad qué tiene que decidir.

## Notas

(Todavía no hay ninguna.)

### 2026-11-05 · DataEngineerML → Laia · Tareas 2.3 y 2.4 hechas; riego aún sin aplicar a la tabla real
- **Qué:** `training_table.build_training_table(..., installation=, irrigation=)` ya excluye por horizonte los periodos con riego marcado y los días de instalación (origen y objetivo); los `change_1d` / `change_3d` apoyados en un día de instalación pasan a nulo sin quitar el ejemplo. Añadidas `doy_sin` y `doy_cos` (`seasonal_features`, reutilizable por la inferencia). La lista manual incluye 5337 y 5515. `prepare_training_table.py` aplica la instalación; el riego se aplicará cuando esté la lluvia prevista (1.4). La tabla `data/agrodevices/training_table.parquet` NO se ha regenerado todavía.
- **Contrato:** Ninguno.
- **Necesito de ti:** que confirmes si regenero la tabla con la exclusión de instalación (cifras en el informe) y el texto propuesto para `docs/decisiones.md`.
- **Estado:** abierta

### 2026-11-05 · DataEngineerML → Laia · Capas de datos bronce, plata y oro hechas
- **Qué:** nuevo `src/irrigation_app/data_layers.py` con las rutas (`SILVER_DIR`, `GOLD_DIR` y cada archivo); los cuatro `prepare_*` las usan. Archivos movidos (no regenerados): `data/silver/` (units, readings, daily, snapshots, muestra_unidad_1130.csv) y `data/gold/training_table.parquet`; `data/agrodevices/` ya no existe. `scripts/describe_data_layers.py` genera un manifiesto por capa (`data/<capa>/manifest.json`, sin datos) y `docs/capas_de_datos.md` explica el flujo con las cifras reales. Pendiente a propósito: `scripts/download_weather.py` y `data/weather/` (descarga en marcha) siguen sin tocar; `download_weather.py` aún apunta a `data/agrodevices/` por defecto, así que hay que cambiarlo a `UNITS_PATH` y `DAILY_PATH` antes de volver a ejecutarlo. Los exports originales se indican con `--bronze-dir` o `AGRODEVICES_EXPORTS_DIR`. `docs/decisiones.md` sigue citando las rutas viejas (`data/agrodevices/`): propongo actualizarlas a `data/silver/` y `data/gold/`.
- **Contrato:** Ninguno.
- **Necesito de ti:** que apruebes el cambio de rutas en `docs/decisiones.md` y que me avises cuando termine la descarga de meteorología para migrarla a las capas.
- **Estado:** abierta

### 2026-11-05 · DataEngineerML → Laia, AgronomoFAO56 y ReportUI · Los días con lecturas SMMAdmin cuentan como instalación
- **Qué:** nueva regla aprobada por Laia: un día UTC de unidad con alguna lectura `SMMAdmin` (en cualquier profundidad, aunque sea parcial) es día de instalación, en unión con la regla automática y la lista manual. `irrigation_marks.admin_days(daily)` saca esos días de `n_admin` en `data/silver/daily.parquet` y `installation_days(..., admin=)` los une; `scripts/prepare_training_table.py` la aplica (nuevo argumento `--daily`). Días de instalación: 732 (97 unidades) -> 996 (105 unidades); 74 unidades tienen SMMAdmin (463 días), 264 días son nuevos en 59 unidades. Tabla de oro regenerada: 648.148 -> 642.240 ejemplos (123 unidades, igual que antes). La 1168 queda del 9 al 15 de marzo de 2025 (7 días). `data/gold/training_table.parquet` y `data/silver/snapshots.parquet` reescritos; `docs/capas_de_datos.md` y los manifiestos actualizados. 89 pruebas en verde. Aviso para quien cite cifras: los 648.148 y el error de persistencia a 7 días de 2,40 (ahora 2,39) han cambiado.
- **Contrato:** Ninguno (la tabla de oro tiene las mismas columnas).
- **Necesito de ti:** Laia: texto propuesto para `docs/decisiones.md` y para `tasks.md`/specs, que los editas tú: «Un día de unidad con alguna lectura SMMAdmin cuenta como día de instalación; se une a la regla automática (umbral 20, tope 30 días) y a la lista manual (5337, 5515)». Cualquier cifra de `docs/decisiones.md` que cite 648.148 ejemplos queda desfasada.
- **Estado:** abierta

### 2026-11-05 · DataEngineerML → Laia · Tareas 1.3, 0.5 y 3.1 hechas (unión de meteorología, marcador integration, reparto)
- **Qué:** `src/irrigation_app/features.py` une la meteorología a cada ejemplo con `forecast_days`, `weather_features` y `add_weather_features`. Para el horizonte N toma los días origen+0 a origen+N−1, cada uno con su antelación, suma lluvia y ET₀ y promedia temperatura, humedad y viento. Un dato ausente deja la variable nula y `weather_incomplete = True`, sin rellenar. La inferencia (4.4) reutiliza las mismas funciones. `pytest.ini` registra `integration` y `conftest.py` salta esas pruebas si no hay PostgreSQL en `localhost:5433`. `src/irrigation_app/models/split.py` hace el reparto por ubicación y fecha de corte (margen de 7 días, semilla 42, `41.66_0.56` siempre a entrenamiento) y lo guarda en `data/ml/split.json`. Probado solo con datos sintéticos; la unión con la meteorología real (1.4) espera a que termine la descarga.
- **Contrato:** Ninguno.
- **Necesito de ti:** Laia: elegir la fecha de corte. Se recomienda el 31 de julio de 2026 (21,6 % de los ejemplos después del corte; validación y test cubren de agosto a octubre). Con 30/6 serían 29,9 % y con 31/8, 13,2 %.
- **Estado:** abierta

### 2026-11-05 · AgronomoFAO56 → Laia, DataEngineerML y ReportUI · Tareas 5.1, 5.2 y 5.3 hechas (C5 disponible)
- **Qué:** `reference_plot.py` (REFERENCE_PLOT, 1168 confirmada; modelos Sourced/Anchor/Anchors/ReferencePlot), `irrigation_engine/reference_values.py` (tablas FAO-56 con fuente y estado verified/assumption; UnknownReferenceError ante cultivo, textura o riego desconocidos) y `scripts/compute_anchors.py` (muestra las líneas de Agrodevices convertidas, 79,4 y 59,7, frente a los percentiles de la media 15/25/35 cm sin días de instalación; máximo real 76,7). Anclajes en REFERENCE_PLOT con review="pending" hasta que Laia los confirme. Supuestos por confirmar: Ea genérica 0,75, punto medio de TAW (155 mm/m) y de fw (0,35), caso de Kc sin cubierta y con helada, Zr 1,0 m.
- **Contrato:** C5 definido (campos: unit_id, status, crop/soil_texture/irrigation_type/cycle_start_date/area_m2 como Sourced o None, average_depths_cm, smm_min/smm_max, anchors.upper/lower con index, source, method, review). C2 aún sin crear (fase 6).
- **Necesito de ti:** Laia: confirmar anclajes y supuestos. DataEngineerML: nada; si cambia `installation_days`, la referencia sigue válida. ReportUI: nada todavía.
- **Estado:** abierta

### 2026-11-05 · AgronomoFAO56 → todos · Anclajes confirmados y cubierta vegetal como supuesto (C5)
- **Qué:** Laia confirma los anclajes de Agrodevices: superior 79,4 (línea 7889) e inferior 59,7 (línea 8834). Ya están `confirmed` en `REFERENCE_PLOT` (`reviewed_by="Laia"`, `reviewed_on=2026-11-05`); `compute_anchors.py` lo imprime. Supuestos aceptados por Laia: Zr 1,0 m, agua útil 155 mm/m, fw 0,35; eficiencia 0,90 con goteo (0,75 solo si faltara el tipo de riego). Nuevo campo `REFERENCE_PLOT.ground_cover` (`active_cover` o `no_cover`, con origen): en la 1168 es `active_cover`, origen `assumption` (supuesto de Laia, sin confirmar; se preguntará a Agrodevices). `reference_values.kc_case()` da Kc 0,50 / 1,20 / 0,95 (tabla 12) con cubierta, frente a 0,45 / 0,95 / 0,70 sin ella. El motor añadirá la limitación `assumed_ground_cover` con `ground_cover_limitation()`: sin cubierta la dosis de pleno verano baja un 21 %.
- **Contrato:** C5 amplía `REFERENCE_PLOT` con `ground_cover` y con `reviewed_by` y `reviewed_on` en cada anclaje.
- **Necesito de ti:** ReportUI: el informe debe mostrar `assumed_ground_cover` junto a `assumed_crop_stage`. DataEngineerML: nada.
- **Estado:** abierta

### 2026-11-05 · DataEngineerML → Laia · Reparto de datos generado con corte 31/07/2026 (3.1 cerrada)
- **Qué:** `data/ml/split.json` generado con corte 2026-07-31, margen 7 días, semilla 42. 14 de 73 ubicaciones apartadas para test (26 unidades): 41.44_-5.30, 41.44_0.65, 41.58_0.65, 41.67_1.13, 41.72_0.98, 41.79_0.97, 41.88_0.23, 41.90_0.23, 41.90_0.24, 41.91_0.25, 41.96_-0.31, 41.99_0.20, 41.99_0.21, 42.00_-0.66. `41.66_0.56` queda en entrenamiento. Ejemplos: entrenamiento 373.302, validación 94.944, test 43.865 y 130.129 sin asignar (margen y apartadas anteriores al corte). Por horizonte (1 a 7 días), el test va de 6.582 a 5.955. Recargar con `load_split` da la misma asignación. Añadida una prueba con un `split.json` de ejemplo.
- **Contrato:** Ninguno. `split.json` queda ignorado por git, igual que el resto de `data/`.
- **Necesito de ti:** Nada. Para 3.4, la evaluación sin unidades `shared` es adicional a este reparto.
- **Estado:** cerrada

### 2026-11-05 · DataEngineerML → Laia, AgronomoFAO56 y ReportUI · Meteorología real unida; capas de meteorología; tabla de oro regenerada (1.2 y 1.4 hechas)
- **Qué:** descarga de Open-Meteo completa y comprobada: 73 de 73 ubicaciones, 182.749 filas, antelaciones 0 a 6, cobertura del 100 % de los días con lecturas (20.064 pares ubicación-día), sin nulos ni negativos. Meteorología en capas: bronce `data/bronze/weather/previous_runs/` (un Parquet por ubicación) y plata `data/silver/weather_previous_runs.parquet`, `weather_locations.parquet` y `weather_unit_locations.parquet` (`data/weather/` ya no existe). `scripts/prepare_training_table.py` une la meteorología (`features.py`, sin cambios) y excluye los riegos marcados con los valores de partida (subida de más de 2 puntos, lluvia < 2 mm, media de profundidades; nuevo `irrigation_marks.interval_rain`). Tabla de oro regenerada: 642.240 -> 554.551 ejemplos (por horizonte 90.308, 85.623, 81.687, 78.302, 75.376, 72.811, 70.444), 17 columnas, parámetros en `data/gold/training_table_params.json`. `split.json` sin cambios (mismas 14 ubicaciones); conjuntos: entrenamiento 330.131, validación 78.757, test 36.737. Persistencia sobre esta tabla: 0,71 / 1,01 / 1,24 / 1,42 / 1,58 / 1,73 / 1,84 (1 a 7 días); la vara de medir cambia. Propuesta de umbral, pendiente de Laia: 3 puntos y 2 mm (582.328 ejemplos; persistencia 0,74 a 1,90).
- **Contrato:** Ninguno (la tabla de oro añade columnas meteorológicas; C3 sigue igual).
- **Necesito de ti:** Laia: aprobar el umbral (2.5) y los textos de `decisiones.md`. AgronomoFAO56: revisar que una subida de 3 puntos (media de profundidades) con lluvia < 2 mm tenga sentido agronómico como regla de riego marcado.
- **Estado:** abierta

### 2026-11-05 · AgronomoFAO56 → Laia, DataEngineerML y ReportUI · Fase 6 hecha (6.1 a 6.4 y 6.6): contrato C2 disponible
- **Qué:** `irrigation_engine/` listo: `inputs.py` (EngineInput), `result.py` (EngineResult), `calculation.py` (`run_engine`) y `reference_values.py` ampliado (Kc por día, inicio de ciclo supuesto 1 de marzo). Documentado en `docs/arquitectura.md` 5.2. Ejemplo sintético: día 4, dosis neta 81,8 mm y bruta 90,9 mm, `provisional`, sensibilidad 73,7 a 82,9 mm netos. Fuera de campaña: `do_not_irrigate` + `out_of_season`.
- **Contrato:** C2 definido. El día k del plan es la fecha origen+k y usa el horizonte k. El resultado trae: plan (decisión, humedad media, D, p, etapa, Kc, ET₀, ETc, lluvia, dosis neta/bruta/volumen), `validation_status`, `indicative`, `factors`, `limitations` (código, texto, parámetros, si degrada), `sensitivity` y `period_start`/`period_end` (= origen+1 y origen+7). C5 amplía `Anchors` con `observed_max_index` (76,7).
- **Necesito de ti:** DataEngineerML: (1) recortar `moisture_pred` a 0-100 antes de guardar (el motor rechaza valores fuera); (2) `limitations` de `ml_prediction` como objetos con `code` y parámetros, que el motor copia tal cual; (3) pedir 8 días de previsión (antelaciones 0 a 7) y tener en cuenta que `period_start` = origen+1 (la tarea 6.5 guarda el resultado en `recommendation`). ReportUI: mostrar `assumed_ground_cover`, `assumed_crop_stage`, `upper_anchor_above_observed_max`, `localized_irrigation` y `out_of_season`; los mm son sobre la franja mojada; no hay duración; el volumen solo existe con superficie. Laia: decidir (a) criterio de dosis en goteo, (b) inicio de campaña 1 de marzo y fin 26 de septiembre, (c) umbral de aplazar por lluvia (5 mm).
- **Estado:** abierta

### 2026-11-05 · DataEngineerML → Laia, AgronomoFAO56 y ReportUI · Umbral aplicado, tabla de oro final y modelos entrenados (2.5, 3.2, 3.3 y 3.4 hechas)
- **Qué:** tabla de oro regenerada con subida de 3 puntos, lluvia mínima 2 mm y media de profundidades: 582.328 ejemplos (por horizonte 91.764, 88.330, 85.314, 82.582, 80.196, 78.075, 76.067), 775 días de unidad con riego marcado en 112 unidades. Persistencia 0,74 / 1,04 / 1,29 / 1,48 / 1,64 / 1,80 / 1,90 (1 a 7 días). `split.json` sin cambios (mismas 14 ubicaciones; entrenamiento 344.311, validación 84.219, test 39.433). Nuevos `models/training.py`, `models/evaluation.py`, `models/experiment.py` y `scripts/train_models.py`; modelos en `data/ml/models/`, métricas en `data/ml/metrics.csv` (32 s en CPU). En test, LightGBM mejora el MAE de la persistencia entre +4,6 % y +8,3 % en los 7 horizontes (también sin las unidades shared); Ridge no la bate en MAE (-3 a -5 %). Quitar la meteorología apenas cambia el MAE de LightGBM (RMSE de test 2 a 3 % peor). Hiperparámetros elegidos solo con validación.
- **Contrato:** C4 (`ml_model.metrics`) ya puede alimentarse de `data/ml/metrics.csv` y `data/ml/models/summary.json`; sin cambios en C1 ni C3. Para 4.x: el nivel previsto se calcula sumando el cambio a `base_index` y hay que recortarlo a 0-100 antes de guardar.
- **Necesito de ti:** Laia: decidir si LightGBM se adopta como modelo de producción, si se hace una ronda más de mejora o se deja así, y si se reentrena con entrenamiento + validación. AgronomoFAO56: su revisión de la regla de riego (3 puntos y 2 mm) está entregada: aprueba y propone añadir la lluvia del propio día y comparar con 2 puntos.
- **Estado:** abierta

### 2026-11-05 · AgronomoFAO56 → Laia, DataEngineerML y ReportUI · Dosis del goteo por reposición de la ETc (C2 ampliado)
- **Qué:** con goteo, la dosis neta del evento es el menor de: ETc de los días del intervalo de reposición (3 días supuesto, declarable con `EngineInput.replenishment_interval_days`), agotamiento actual (D×TAW×Zr) y tope RAW = p×TAW×Zr. La sonda sigue decidiendo cuándo. Ejemplo del día 4 (25-08-2026, ET₀ 5): antes 81,8 neta / 90,9 bruta, ahora 17,6 / 19,6 mm (sensibilidad 17,6 a 17,9 netos). Riego no localizado o desconocido: se mantiene el llenado a capacidad de campo. `METHOD_VERSION` pasa a `fao56-engine-2`. Inicio 1 de marzo, fin 26 de septiembre y umbral de lluvia 5 mm quedan aprobados por Laia (5 de noviembre de 2026). Documentado en `docs/arquitectura.md` 5.2. 218 pruebas en verde.
- **Contrato:** C2 amplía `DayPlan` (`depletion_mm`, `raw_cap_mm`, `etc_interval_mm`, `dose_limit`), `EngineResult` (`dose_criterion`, `replenishment_interval_days`, `dose_on_wetted_strip`) y `EngineInput` (`replenishment_interval_days`, opcional). Limitaciones nuevas: `assumed_replenishment_interval` (degrada), `drip_replenishment_dose`, `dose_limited`. `upper_anchor_above_observed_max` añade `depletion_overestimate_mm`.
- **Necesito de ti:** ReportUI: mostrar el intervalo supuesto y que los mm son sobre la franja mojada; explicar que los anclajes mueven el día más que los mm; actualizar los textos que citaban 81,8/90,9 o «vuelve a capacidad de campo». Laia: decidir intervalo (3 días), si quiere un tope más estricto que RAW y si se descuenta la lluvia del intervalo.
- **Estado:** abierta

### 2026-11-05 · DataEngineerML → Laia, AgronomoFAO56 y ReportUI · Regla de lluvia del día d aplicada; modelos reentrenados; sensibilidad 2 frente a 3 puntos
- **Qué:** `irrigation_marks.interval_rain` descarta una subida si la lluvia prevista (antelación 0) es de 2 mm o más el día d-1 o el día d (aproximación con datos diarios, UTC completo, no 05:00 a 05:00). Tabla de oro regenerada con 3 puntos y 2 mm: 587.032 ejemplos (antes 582.328), 712 días de unidad con riego marcado (antes 775), persistencia 0,76 a 1,94 (1 a 7 días); `split.json` idéntico. Reentrenado con `scripts/train_models.py` (hiperparámetros por validación; sin reentrenar con validación): en test LightGBM mejora el MAE de la persistencia entre +4,7 % y +8,6 % (entre +5,0 % y +7,5 % sin shared); Ridge no la bate (-2,5 % a -3,7 %). Sensibilidad con 2 puntos en `data/ml/sensitivity_2pts/` (1.063 días marcados, 561.109 ejemplos). Sobre el test común (36.893 ejemplos) los dos LightGBM mejoran la persistencia en +6,2 % a +9,9 % (3 puntos) y +6,8 % a +10,5 % (2 puntos): diferencia de MAE de 0,000 a 0,013, dentro del ruido. El test se ha mirado en tres ocasiones: 3 puntos solo con d-1, 3 puntos final y 2 puntos.
- **Contrato:** C4 sin cambios de forma; `data/ml/metrics.csv` y `data/ml/models/summary.json` tienen los números nuevos. C1 y C3 sin cambios.
- **Necesito de ti:** Laia: decir si se hace la ronda de mejora (el alfa de Ridge está en el extremo de la rejilla; variables por día). ReportUI: el error típico por horizonte debe leerse del `metrics.csv` nuevo.
- **Estado:** abierta
