# Capas de datos: bronce, plata y oro

Los datos del proyecto se organizan en tres capas (arquitectura «medallón»). Cada capa parte de la anterior, es un poco más limpia y más cercana a lo que usa el modelo, y se puede reconstruir desde la capa de abajo con un script. Así cada paso se puede revisar por separado y, si cambia una regla de limpieza, solo se rehace lo que depende de ella.

Las cifras de este documento se calcularon el 5 de noviembre de 2026 leyendo los archivos reales con `scripts/describe_data_layers.py`. Los datos viven en `data/`, que **no se sube a git** (son datos de la empresa). Las rutas de cada capa están en un solo sitio: `src/irrigation_app/data_layers.py`.

## El flujo

```
BRONCE                      PLATA                                   ORO
(tal como llega,            (limpios y tipados, data/silver/)       (lo que consume el modelo,
 data/bronze/)                                                       data/gold/)

player_data.csv ──┐
profiler_setup.csv┼─ prepare_agrodevices_units.py ──► units.parquet
datos_limpios.csv ┘                                        │
        │                                                  │
        └─ prepare_agrodevices_readings.py ─────► readings.parquet
                                                           │
                              prepare_daily_readings.py ──► daily.parquet
                                                           │

Open-Meteo ─ download_weather.py ─► weather/previous_runs/*.parquet (bronce)
                                 └► weather_previous_runs.parquet, weather_locations.parquet,
                                    weather_unit_locations.parquet (plata)

                              prepare_training_table.py ─► snapshots.parquet ──► training_table.parquet
                              (usa la plata de lecturas y la de meteorología)    (con meteorología y sin riegos)
```

`prepare_training_table.py` produce tres archivos: `snapshots.parquet` (plata, la humedad de las 05:00 UTC), `training_table.parquet` (oro, los ejemplos de entrenamiento) y `training_table_params.json` (oro, los parámetros del riego y los recuentos con los que se generó la tabla).

## Bronce: los exports tal como llegan

Son tres CSV exportados de la plataforma de Agrodevices el 3 de noviembre de 2026. Son confidenciales: **no se copian a `data/` ni al repositorio** y se guardan en una carpeta local fuera del proyecto. No se modifican nunca; todo lo que se hace con ellos ocurre en la capa siguiente.

La capa se documenta con un manifiesto (`data/bronze/manifest.json`, generado por `describe_data_layers.py`) que guarda solo el nombre, el tamaño, el número de filas, el rango de fechas y un hash corto sha256 de cada archivo. Sin ningún dato dentro. El hash sirve para saber, más adelante, si un export ha cambiado.

| Export | Qué contiene | Tamaño | Filas | Fechas | sha256 (corto) |
|---|---|---|---|---|---|
| `datos_limpios_202611031026.csv` | Lecturas de las sondas cada 15 minutos, 5 canales por fila | 461,0 MB | 2.737.152 | 1-6-2024 a 2-11-2026 | `8a7353cbc56f` |
| `player_data_202611031129.csv` | Dispositivos: modelo, coordenadas, si están activos | 36 KB | 492 | no tiene | `6596b63a5c03` |
| `profiler_setup_202611031126.csv` | Profundidades y rango de la escala de cada sonda | 15 KB | 205 | no tiene | `18f3ba2fbff7` |

Para regenerar el manifiesto: `.venv/Scripts/python scripts/describe_data_layers.py --bronze-dir CARPETA_DE_LOS_EXPORTS` (o con la variable de entorno `AGRODEVICES_EXPORTS_DIR`). Si la carpeta no existe, el script lo dice y sigue con las demás capas.

### Bronce: la meteorología tal como la devuelve Open-Meteo

Está en `data/bronze/weather/previous_runs/`: un Parquet por ubicación (`<location_id>.parquet`, por ejemplo `41.56_0.31.parquet`), con lo que devolvió Open-Meteo para esa ubicación más la columna `location_id`. No se modifican. Son **73 archivos (3,5 MB) y 182.749 filas**, del 1-6-2024 al 2-11-2026, con las previsiones de antelación 0 a 6 días (26.107 filas por antelación). Cada ubicación solo tiene los días entre la primera y la última lectura de sus unidades (entre 12 y 885 días). `data/` no se sube a git.

`download_weather.py` escribe aquí un archivo por ubicación y, si se corta (límite de peticiones), al volver a ejecutarlo solo pide las ubicaciones que faltan. El manifiesto de bronce (`data/bronze/manifest.json`) anota estas cifras en su apartado `weather`.

## Plata: datos limpios y tipados

Es el resultado de aplicar la limpieza de Agrodevices. Tipos fijos (enteros, fechas con zona UTC, decimales) y un solo significado por columna. Está en `data/silver/`.

| Archivo | Una fila es… | Filas | Columnas principales | Fechas | Tamaño |
|---|---|---|---|---|---|
| `units.parquet` | una sonda (unidad) | 128 | `unit_id`, coordenadas y `coord_status`, `smm_min` y `smm_max` (rango de la escala), profundidades `depth_cm_1..5`, `n_depths` | no tiene | 17 KB |
| `readings.parquet` | una lectura de 15 minutos a una profundidad | 9.522.149 | `unit_id`, `timestamp`, `depth_cm`, `frequency`, `reading_type` | 1-6-2024 a 2-11-2026 | 30,2 MB |
| `daily.parquet` | un día, una unidad y una profundidad | 100.168 | `frequency_mean/min/max`, `n_readings` (y cuántas son reales, interpoladas y `SMMAdmin`), `moisture_index_mean/min/max` | 1-6-2024 a 2-11-2026 | 2,0 MB |
| `snapshots.parquet` | la humedad de las 05:00 UTC de una unidad, día y profundidad | 99.153 | `unit_id`, `day`, `depth_cm`, `moisture_index`, `n_readings` | 1-6-2024 a 2-11-2026 | 0,3 MB |
| `muestra_unidad_1130.csv` | una muestra pequeña de la unidad 1130 | 864 | 6 columnas (marca de tiempo, tipo y 4 profundidades en frecuencia) | 25-10-2026 a 2-11-2026 | 46 KB |
| `weather_previous_runs.parquet` | un día, una ubicación y una antelación (0 a 6) | 182.749 | `location_id`, `day`, `lead_days`, `precipitation_mm`, `et0_mm`, `temperature_c`, `humidity_pct`, `wind_kmh` | 1-6-2024 a 2-11-2026 | 1,4 MB |
| `weather_locations.parquet` | una ubicación (unidades a menos de 1 km agrupadas) | 73 | `location_id`, coordenadas, `n_units`, `has_shared_coordinates` | no tiene | 5 KB |
| `weather_unit_locations.parquet` | una unidad y su ubicación | 128 | `unit_id`, `location_id` | no tiene | 3 KB |

Todas las tablas con lecturas cubren las mismas **128 unidades** (507 pares unidad-profundidad). Las unidades tienen 4 profundidades en 107 casos, 5 en 9 y 3 en 12; 103 tienen coordenada propia (`ok`) y 25 comparten ubicación (`shared`).

**Reglas de calidad aplicadas** (el detalle y las cifras de cada decisión están en `docs/decisiones.md`):

- **De 481 a 128 unidades.** Se queda solo con las sondas de humedad (modelo `SMM`: 262), activas (245), que no sean de demostración (243), con lecturas (176) y con coordenadas reales (128). Sin coordenadas no se puede pedir la meteorología.
- **`SMM0` excluido.** Son filas con todos los canales a 0: dato erróneo o faltante. Se descartan 103.062 filas de ese tipo, además de 194.880 de unidades que ya no están. Quedan 2.439.210 filas, que al pasar de cinco canales por fila a una lectura por profundidad dan las 9.522.149 lecturas.
- **`SMMi` conservado con marca.** Son lecturas interpoladas por la plataforma (1,67 M de las 9,52 M); se mantienen y se distinguen en `reading_type` (`SMM` 7,70 M, `SMMi` 1,67 M, `SMMAdmin` 0,15 M). Si se prefiere quitarlas, basta filtrar por esa columna.
- **Escala común.** La frecuencia en bruto no se puede comparar entre sondas, porque cada una tiene su rango. En `daily.parquet` se reescala linealmente a un **índice de 0 a 100** por sonda (0 seco, 100 húmedo). Es un índice relativo al rango de la sonda, no humedad volumétrica.
- **Hora de origen: 05:00 UTC.** `snapshots.parquet` guarda la lectura de las 05:00 UTC de cada día, que es el origen de toda predicción.
- **Los valores desconocidos son nulos**, no cifras supuestas.
- **Meteorología comprobada el 5 de noviembre de 2026.** Reúne los 73 archivos de bronce sin cambiar ningún valor. Cubre el 100 % de los días con lecturas (20.064 pares ubicación-día) en las siete antelaciones; no hay lluvia ni ET₀ negativas, ni nulos, ni filas repetidas. Hay 6.043 pares ubicación-día descargados sin lecturas (huecos dentro del periodo de cada ubicación): no se usan. El máximo de lluvia (238,5 mm el 29-10-2024 en la zona de Valencia) coincide con la DANA de ese día; no es un error.

## Oro: lo que consumen el modelo y la app

Está en `data/gold/`. Hoy contiene la tabla de entrenamiento y los parámetros con los que se generó.

| Archivo | Una fila es… | Filas | Columnas principales | Fechas | Tamaño |
|---|---|---|---|---|---|
| `training_table.parquet` | una sonda-profundidad, un día de origen y un horizonte (1 a 7 días) | 587.032 | `unit_id`, `depth_cm`, `origin_day`, `horizon_days`, `base_index`, `change_1d`, `change_3d`, `doy_sin`, `doy_cos`, `target_index`, `target_change`, y la meteorología: `precip_sum_mm`, `et0_sum_mm`, `temp_mean_c`, `humidity_mean_pct`, `wind_mean_kmh`, `weather_incomplete` | origen: 1-6-2024 a 1-11-2026 | 10,9 MB |
| `training_table_params.json` | los parámetros y recuentos con los que se generó la tabla | — | umbral y lluvia del riego, instalación, recuentos por horizonte | — | pocos KB |

Cubre **123 unidades** y 488 pares unidad-profundidad. Las 5 unidades de plata que no llegan al oro (1157, 5256, 5270, 5502 y 5519) tienen entre 1 y 11 días con datos, y casi todos son días de instalación. Ejemplos por horizonte, de 1 a 7 días: 91.986, 88.752, 85.910, 83.307, 81.026, 78.997 y 77.054.

**Reglas de calidad aplicadas:**

- **Días de instalación fuera.** Una sonda recién puesta da valores de 0 a 15 hasta que se entierra, y esos valores no son sequedad. Se excluyen como día de origen y como día objetivo (996 días en 105 unidades). Es la unión de tres reglas de `irrigation_marks.py`: la automática (índice por debajo de 20 al empezar, tope de 30 días), la lista manual de las unidades 5337 y 5515, y los días con alguna lectura `SMMAdmin` (463 días en 74 unidades; el día es el día UTC de las fotos de las 05:00). Quita 21.243 de los 663.483 ejemplos iniciales.
- **Riegos marcados fuera, por horizonte.** Un ejemplo de horizonte N se quita si hay un riego marcado entre los días origen+1 y origen+N (el modelo predice «sin riego futuro»). Un riego es una subida de la media de las profundidades de más de 3 puntos entre dos madrugadas consecutivas sin lluvia que la explique: la subida no se marca si la lluvia prevista (antelación 0) es de 2 mm o más el día d−1 **o** el día d (la lluvia de madrugada, de 00:00 a 05:00 UTC, sube el índice sin ser riego). Es una **aproximación con datos diarios**: la lluvia es la del día UTC completo, no la del tramo de 05:00 a 05:00, así que se descarta algún riego de más (`interval_rain` toma el máximo de los dos días). Con estos valores (decididos por Laia el 5 de noviembre de 2026) hay 712 días de unidad con riego marcado (111 unidades; con la regla anterior, solo el día d−1, eran 775) y se quitan 55.208 ejemplos: queda el 97 % de los ejemplos a 1 día y el 86 % a 7 (tras quitar la instalación). Los valores usados se guardan en `training_table_params.json`.
- **El modelo predice el cambio de humedad** (`target_change`, la diferencia del índice entre el origen y el día objetivo), no el valor absoluto. La persistencia, que es «no cambia nada», es la vara de medir: su error medio sobre esta tabla es 0,76 puntos a 1 día y 1,94 a 7 días (antes de quitar los riegos era 0,94 y 2,39).
- **Los cambios apoyados en un día de instalación pasan a nulo** (`change_1d`, `change_3d`) sin quitar el ejemplo.
- **Meteorología unida sin rellenar.** Para el horizonte N, suma de lluvia y de ET₀ y media de temperatura, humedad y viento de los días origen a origen+N−1, cada día con su antelación (0 a N−1), de la ubicación de la unidad (`features.py`). Si faltara un dato, la variable queda nula y `weather_incomplete` verdadero; hoy no falta ninguno (0 de 587.032).

Recuento por horizonte (sale por consola al ejecutar `prepare_training_table.py`):

| Horizonte | Inicial | Quitados por instalación | Quitados por riego | Quitados por meteorología | Final |
|---|---|---|---|---|---|
| 1 día | 97.543 | 3.144 | 2.413 | 0 | 91.986 |
| 2 días | 96.486 | 3.078 | 4.656 | 0 | 88.752 |
| 3 días | 95.562 | 3.041 | 6.611 | 0 | 85.910 |
| 4 días | 94.661 | 3.031 | 8.323 | 0 | 83.307 |
| 5 días | 93.844 | 3.009 | 9.809 | 0 | 81.026 |
| 6 días | 93.081 | 2.978 | 11.106 | 0 | 78.997 |
| 7 días | 92.306 | 2.962 | 12.290 | 0 | 77.054 |

## Qué queda pendiente

- **Parámetros del riego.** Los definitivos (3 puntos, 2 mm, media de profundidades, lluvia de los días d−1 y d) están aplicados desde el 5 de noviembre de 2026. El análisis de sensibilidad con 2 puntos está aparte, en `data/ml/sensitivity_2pts/` (561.109 ejemplos), y no forma parte de las capas.
- **`ml_prediction` y `recommendation`.** Son el resultado del modelo y del motor de riego. Irán en la capa oro, pero dentro de la base de datos y no en archivos (ver `docs/base_de_datos.md`).
