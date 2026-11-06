# Meteorología (Open-Meteo)

Conector que obtiene la meteorología que necesita el proyecto: las variables diarias con las que se entrena el modelo de humedad y las que se usan al predecir. Está en este módulo, con su propia documentación, para que se pueda entender sin leer el resto del proyecto.

> **Origen.** El conector parte de un servicio previo de la autora, adaptado y reescrito para este proyecto.

## Qué hace

- Pide a Open-Meteo, para cada ubicación de las unidades de sensores, los datos meteorológicos por horas y los resume en valores diarios.
- Tres fuentes de Open-Meteo, según para qué se use:
  - **Previsiones archivadas** (*Previous Runs*): lo que el modelo meteorológico había previsto 1, 3 o 7 días antes. Es lo que se usa para **entrenar**, porque al predecir en producción solo habrá previsiones.
  - **Previsión** actual, para predecir cada día.
  - **Reanálisis** (datos observados), para comparar y para los días recientes.
- Variables diarias: lluvia (suma), ET₀ de FAO-56 (suma), temperatura, humedad relativa y viento (medias).

## Qué se ha comprobado (4 de noviembre de 2026)

- **Previous Runs** devuelve datos por horas completos (lluvia, ET₀, temperatura, humedad y viento) con 0, 1, 3 y 7 días de antelación desde junio de 2024 (la 0, que es la ejecución más reciente, se comprobó el 4 de noviembre de 2026: viene completa). En enero de 2024 los valores vienen vacíos. Cubre todo el periodo de las sondas.
- **Agregar por horas es válido:** en la previsión, el valor diario de Open-Meteo coincide con la suma (lluvia, ET₀) o la media (temperatura, humedad, viento) de sus 24 valores horarios. La diferencia máxima es de 0,02 mm en la ET₀, por redondeo.
- Las peticiones aceptan un rango de fechas. **Probado con una ubicación:** 885 días (junio de 2024 a noviembre de 2026) con las antelaciones de 1, 3 y 7 días (15 variables) en una sola petición, unos 8 s, sin ningún hueco; y 5 días con las 7 antelaciones de 0 a 6 (35 variables), sin huecos. Con las 35 variables, una petición de 885 días se agotó tres veces (120 s cada una): el servidor a veces responde muy lento (otra petición tardó 28 s y otra pasó de 60 s). Por eso el conector parte los rangos largos en trozos de 60 días (cuestan lo mismo de cuota), espera hasta 120 s por petición y reintenta hasta 3 veces. El partido en trozos está probado sin red; con datos reales se comprobará en la primera descarga.

## Por qué hay varias antelaciones (`lead_days`)

La antelación es cuántos días antes del día previsto se hizo la previsión (0 es la del propio día). Para el viernes, la previsión del jueves (1 día) es mejor que la del martes (3 días). El modelo de humedad predice el cambio entre las 05:00 UTC de hoy y las de dentro de N días (N de 1 a 7), así que le importa el tiempo de los días de hoy a hoy+N−1, y en producción solo hay la previsión de hoy, que para cada uno de esos días tiene justo la antelación 0, 1, 2… Si se entrenara con la previsión más exacta (la de 1 día para todos), el modelo se fiaría de una calidad que no tendrá al usarlo y fallaría. Por eso **cada día se toma con su antelación** y el conector pide todas, de 0 a 6. (Una versión anterior pedía de 1 a 7 y dejaba fuera el tiempo del propio día de origen; se corrigió el 4 de noviembre de 2026 antes de descargar.)

## Uso

```python
from datetime import date
from irrigation_app.weather.open_meteo import fetch_previous_runs

fetch_previous_runs(41.56, 0.31, date(2024, 6, 1), date(2026, 11, 2))  # antelaciones de 0 a 6 días
```

Devuelve una fila por día y antelación con `precipitation_mm`, `et0_mm`, `temperature_c`, `humidity_pct` y `wind_kmh`. Un día solo tiene valor si Open-Meteo dio sus 24 horas; los días sin ningún valor no se devuelven. Los errores no incluyen la URL, porque lleva las coordenadas.

### Previsión actual (producción)

```python
from irrigation_app.weather.open_meteo import fetch_forecast

fetch_forecast(41.99, 0.21)  # 8 días desde hoy (UTC), antelaciones de 0 a 7
```

En producción no hay Previous Runs: la previsión de hoy es la antelación 0 para hoy, 1 para mañana, etc. Devuelve las mismas columnas y unidades que `fetch_previous_runs`, con el día UTC y sus 24 horas completas (un día incompleto no se devuelve, nunca se rellena). Pide 5 variables para 8 días: una sola llamada de la cuota. Son 8 días y no 7 porque el modelo usa de origen a origen+6 pero el motor de riego usa de origen+1 a origen+7. Comprobado en vivo el 6 de noviembre de 2026 para la 1168: las variables de `features.py` desde la previsión actual y desde Previous Runs (para el mismo origen) coinciden con diferencias de hasta 0,2 mm en lluvia y ET₀ acumuladas.

### Guardarla en la base de datos (`forecast_store.py`, contrato C3)

`store_forecast` guarda en `forecast_data` la previsión de un origen (8 filas: el día origen+k con antelación k) y `load_forecast` la devuelve con los nombres de esa tabla (`rainmm`, `eto`, `tmedia`, `rhmedia`, `vientomedia`). La columna `source` es `forecast` (previsión actual) o `previous_runs` (archivadas, para repetir un día pasado). Reglas: el día es el día UTC (como el entrenamiento); repetir un origen no cambia lo guardado, solo rellena huecos; las columnas que el proyecto no tiene quedan en nulo.

## Qué no incluye

No es un servicio: no tiene API propia, ni tareas programadas, ni caché. Quien lo use (el proceso de entrenamiento o la tarea diaria) lo llama como una función.

## Límites de uso

La capa gratuita de Open-Meteo admite 600 llamadas por minuto, 5.000 por hora y 10.000 al día, solo para uso no comercial. **Una petición grande cuenta como varias llamadas**: más de 10 variables o más de 14 días se pesan en proporción (aproximadamente variables/10 × días/14). Cada antelación de cada variable cuenta como una variable, así que las 5 variables con las 7 antelaciones son 35.

Pedir 885 días para cada una de las 73 ubicaciones costaría unas 16.000 llamadas, más que el límite diario. Por eso el script de descarga pide a cada ubicación solo los días en que sus unidades tienen lecturas (mediana de 248 días): unas **6.500 llamadas**. Los fallos pasajeros (502, 503, 504 y «demasiadas peticiones a la vez») se reintentan; un límite de uso por hora o por día no, porque no sirve de nada.
