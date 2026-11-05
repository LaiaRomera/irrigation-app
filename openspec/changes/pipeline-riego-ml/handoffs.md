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
