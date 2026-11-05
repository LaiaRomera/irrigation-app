# Capas de datos: bronce, plata y oro

Los datos del proyecto se organizan en tres capas (arquitectura «medallón»). Cada capa parte de la anterior, es un poco más limpia y más cercana a lo que usa el modelo, y se puede reconstruir desde la capa de abajo con un script. Así cada paso se puede revisar por separado y, si cambia una regla de limpieza, solo se rehace lo que depende de ella.

Las cifras de este documento se calcularon el 5 de noviembre de 2026 leyendo los archivos reales con `scripts/describe_data_layers.py`. Los datos viven en `data/`, que **no se sube a git** (son datos de la empresa). Las rutas de cada capa están en un solo sitio: `src/irrigation_app/data_layers.py`.

## El flujo

```
BRONCE                      PLATA                                   ORO
(exports originales,        (limpios y tipados, data/silver/)       (lo que consume el modelo,
 fuera del repositorio)                                              data/gold/)

player_data.csv ──┐
profiler_setup.csv┼─ prepare_agrodevices_units.py ──► units.parquet
datos_limpios.csv ┘                                        │
        │                                                  │
        └─ prepare_agrodevices_readings.py ─────► readings.parquet
                                                           │
                              prepare_daily_readings.py ──► daily.parquet
                                                           │
                              prepare_training_table.py ─► snapshots.parquet ──► training_table.parquet
```

`prepare_training_table.py` produce dos archivos: `snapshots.parquet` (plata, la humedad de las 05:00 UTC) y `training_table.parquet` (oro, los ejemplos de entrenamiento).

## Bronce: los exports tal como llegan

Son tres CSV exportados de la plataforma de Agrodevices el 3 de noviembre de 2026. Son confidenciales: **no se copian a `data/` ni al repositorio** y se guardan en una carpeta local fuera del proyecto. No se modifican nunca; todo lo que se hace con ellos ocurre en la capa siguiente.

La capa se documenta con un manifiesto (`data/bronze/manifest.json`, generado por `describe_data_layers.py`) que guarda solo el nombre, el tamaño, el número de filas, el rango de fechas y un hash corto sha256 de cada archivo. Sin ningún dato dentro. El hash sirve para saber, más adelante, si un export ha cambiado.

| Export | Qué contiene | Tamaño | Filas | Fechas | sha256 (corto) |
|---|---|---|---|---|---|
| `datos_limpios_202611031026.csv` | Lecturas de las sondas cada 15 minutos, 5 canales por fila | 461,0 MB | 2.737.152 | 1-6-2024 a 2-11-2026 | `8a7353cbc56f` |
| `player_data_202611031129.csv` | Dispositivos: modelo, coordenadas, si están activos | 36 KB | 492 | no tiene | `6596b63a5c03` |
| `profiler_setup_202611031126.csv` | Profundidades y rango de la escala de cada sonda | 15 KB | 205 | no tiene | `18f3ba2fbff7` |

Para regenerar el manifiesto: `.venv/Scripts/python scripts/describe_data_layers.py --bronze-dir CARPETA_DE_LOS_EXPORTS` (o con la variable de entorno `AGRODEVICES_EXPORTS_DIR`). Si la carpeta no existe, el script lo dice y sigue con las demás capas.

## Plata: datos limpios y tipados

Es el resultado de aplicar la limpieza de Agrodevices. Tipos fijos (enteros, fechas con zona UTC, decimales) y un solo significado por columna. Está en `data/silver/`.

| Archivo | Una fila es… | Filas | Columnas principales | Fechas | Tamaño |
|---|---|---|---|---|---|
| `units.parquet` | una sonda (unidad) | 128 | `unit_id`, coordenadas y `coord_status`, `smm_min` y `smm_max` (rango de la escala), profundidades `depth_cm_1..5`, `n_depths` | no tiene | 17 KB |
| `readings.parquet` | una lectura de 15 minutos a una profundidad | 9.522.149 | `unit_id`, `timestamp`, `depth_cm`, `frequency`, `reading_type` | 1-6-2024 a 2-11-2026 | 30,2 MB |
| `daily.parquet` | un día, una unidad y una profundidad | 100.168 | `frequency_mean/min/max`, `n_readings` (y cuántas son reales, interpoladas y `SMMAdmin`), `moisture_index_mean/min/max` | 1-6-2024 a 2-11-2026 | 2,0 MB |
| `snapshots.parquet` | la humedad de las 05:00 UTC de una unidad, día y profundidad | 99.153 | `unit_id`, `day`, `depth_cm`, `moisture_index`, `n_readings` | 1-6-2024 a 2-11-2026 | 0,3 MB |
| `muestra_unidad_1130.csv` | una muestra pequeña de la unidad 1130 | 864 | 6 columnas (marca de tiempo, tipo y 4 profundidades en frecuencia) | 25-10-2026 a 2-11-2026 | 46 KB |

Todas las tablas con lecturas cubren las mismas **128 unidades** (507 pares unidad-profundidad). Las unidades tienen 4 profundidades en 107 casos, 5 en 9 y 3 en 12; 103 tienen coordenada propia (`ok`) y 25 comparten ubicación (`shared`).

**Reglas de calidad aplicadas** (el detalle y las cifras de cada decisión están en `docs/decisiones.md`):

- **De 481 a 128 unidades.** Se queda solo con las sondas de humedad (modelo `SMM`: 262), activas (245), que no sean de demostración (243), con lecturas (176) y con coordenadas reales (128). Sin coordenadas no se puede pedir la meteorología.
- **`SMM0` excluido.** Son filas con todos los canales a 0: dato erróneo o faltante. Se descartan 103.062 filas de ese tipo, además de 194.880 de unidades que ya no están. Quedan 2.439.210 filas, que al pasar de cinco canales por fila a una lectura por profundidad dan las 9.522.149 lecturas.
- **`SMMi` conservado con marca.** Son lecturas interpoladas por la plataforma (1,67 M de las 9,52 M); se mantienen y se distinguen en `reading_type` (`SMM` 7,70 M, `SMMi` 1,67 M, `SMMAdmin` 0,15 M). Si se prefiere quitarlas, basta filtrar por esa columna.
- **Escala común.** La frecuencia en bruto no se puede comparar entre sondas, porque cada una tiene su rango. En `daily.parquet` se reescala linealmente a un **índice de 0 a 100** por sonda (0 seco, 100 húmedo). Es un índice relativo al rango de la sonda, no humedad volumétrica.
- **Hora de origen: 05:00 UTC.** `snapshots.parquet` guarda la lectura de las 05:00 UTC de cada día, que es el origen de toda predicción.
- **Los valores desconocidos son nulos**, no cifras supuestas.

## Oro: lo que consumen el modelo y la app

Está en `data/gold/`. Hoy contiene la tabla de entrenamiento.

| Archivo | Una fila es… | Filas | Columnas principales | Fechas | Tamaño |
|---|---|---|---|---|---|
| `training_table.parquet` | una sonda-profundidad, un día de origen y un horizonte (1 a 7 días) | 648.148 | `unit_id`, `depth_cm`, `origin_day`, `horizon_days`, `base_index`, `change_1d`, `change_3d`, `doy_sin`, `doy_cos`, `target_index`, `target_change` | origen: 1-6-2024 a 1-11-2026 | 7,2 MB |

Cubre **123 unidades** y 488 pares unidad-profundidad. Las 5 unidades de plata que no llegan al oro (1157, 5256, 5270, 5502 y 5519) tienen entre 1 y 11 días con datos, y casi todos son días de instalación. Ejemplos por horizonte, de 1 a 7 días: 95.264, 94.271, 93.380, 92.481, 91.666, 90.922 y 90.164.

**Reglas de calidad aplicadas:**

- **Días de instalación fuera.** Una sonda recién puesta da valores de 0 a 15 hasta que se entierra, y esos valores no son sequedad. Se excluyen como día de origen y como día objetivo (732 días en 97 unidades, según la regla de `irrigation_marks.py` más la lista manual de las unidades 5337 y 5515). Es lo que baja la tabla de 663.483 a 648.148 ejemplos.
- **El modelo predice el cambio de humedad** (`target_change`, la diferencia del índice entre el origen y el día objetivo), no el valor absoluto. La persistencia, que es «no cambia nada», es la vara de medir: su error medio es 0,94 puntos a 1 día y 2,40 a 7 días.
- **Los cambios apoyados en un día de instalación pasan a nulo** (`change_1d`, `change_3d`) sin quitar el ejemplo.
- **Todavía sin exclusión por riego marcado**: necesita la lluvia prevista para no confundir lluvia con riego (pendiente).

## Qué queda pendiente

- **Meteorología.** La descarga de Open-Meteo sigue en marcha y vive aparte, en `data/weather/`. Cuando termine pasará por las mismas tres capas: lo descargado tal cual será bronce, la tabla unida por ubicación y antelación será plata, y las variables que entran en el modelo se sumarán a la tabla de entrenamiento (oro).
- **`ml_prediction` y `recommendation`.** Son el resultado del modelo y del motor de riego. Irán en la capa oro, pero dentro de la base de datos y no en archivos (ver `docs/base_de_datos.md`).
