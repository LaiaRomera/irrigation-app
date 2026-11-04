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
- Las peticiones aceptan un rango de fechas y varias ubicaciones, y tardan unos 200 ms.

## Qué no incluye

No es un servicio: no tiene API propia, ni tareas programadas, ni caché. Quien lo use (el proceso de entrenamiento o la tarea diaria) lo llama como una función.

## Límites de uso

La capa gratuita de Open-Meteo admite 600 peticiones por minuto, 5.000 por hora y 10.000 al día, solo para uso no comercial. Descargar el histórico de unas 73 ubicaciones son unas 73 peticiones por variable.
