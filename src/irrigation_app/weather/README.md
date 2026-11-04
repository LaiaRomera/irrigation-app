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

- **Previous Runs** devuelve datos por horas completos (lluvia, ET₀, temperatura, humedad y viento) con 1, 3 y 7 días de antelación desde junio de 2024. En enero de 2024 los valores vienen vacíos. Cubre todo el periodo de las sondas.
- **Agregar por horas es válido:** en la previsión, el valor diario de Open-Meteo coincide con la suma (lluvia, ET₀) o la media (temperatura, humedad, viento) de sus 24 valores horarios. La diferencia máxima es de 0,02 mm en la ET₀, por redondeo.
- Las peticiones aceptan un rango de fechas. **Probado con una ubicación:** 885 días (junio de 2024 a noviembre de 2026) con las antelaciones de 1, 3 y 7 días en una sola petición, unos 8 s, sin ningún hueco. A veces el servidor tarda mucho en un momento dado (una petición pasó de 60 s y otra tardó 28 s), por eso el conector espera hasta 120 s y reintenta hasta 3 veces.

## Por qué hay varias antelaciones (`lead_days`)

La antelación es cuántos días antes del día previsto se hizo la previsión. Para el viernes, la previsión del jueves (1 día) es mejor que la del martes (3 días). El modelo de humedad predice los días 1 a 7, y en producción, para el día de hoy más N, solo hay la previsión de hoy, hecha con N días de antelación. Si se entrenara con la de 1 día, que es la más exacta, el modelo se fiaría de una calidad que no tendrá al usarlo y fallaría. Por eso, **para cada horizonte N se entrena con la previsión de N días de antelación**, y el conector pide todas, de 1 a 7.

## Uso

```python
from datetime import date
from irrigation_app.weather.open_meteo import fetch_previous_runs

fetch_previous_runs(41.56, 0.31, date(2024, 6, 1), date(2026, 11, 2))  # antelaciones de 1 a 7 días
```

Devuelve una fila por día y antelación con `precipitation_mm`, `et0_mm`, `temperature_c`, `humidity_pct` y `wind_kmh`. Un día solo tiene valor si Open-Meteo dio sus 24 horas; los días sin ningún valor no se devuelven. Los errores no incluyen la URL, porque lleva las coordenadas.

## Qué no incluye

No es un servicio: no tiene API propia, ni tareas programadas, ni caché. Quien lo use (el proceso de entrenamiento o la tarea diaria) lo llama como una función.

## Límites de uso

La capa gratuita de Open-Meteo admite 600 peticiones por minuto, 5.000 por hora y 10.000 al día, solo para uso no comercial. Descargar el histórico de unas 73 ubicaciones son unas 73 peticiones por variable.
