# Spec Delta

## Purpose

Construir la tabla de ejemplos con la que se entrena y evalúa el modelo de humedad del suelo: el estado del suelo a las 05:00 UTC, la meteorología prevista que habrá al predecir y el cambio de humedad a predecir, sin riegos dentro del periodo a predecir.

## ADDED Requirements

### Requirement: Ejemplo por sonda, origen y horizonte
El sistema SHALL generar un ejemplo por unidad, profundidad, día de origen y horizonte de 1 a 7 días. Solo habrá ejemplo si hay humedad de las 05:00 UTC el día de origen y el día objetivo. El objetivo es el cambio del índice (objetivo menos origen), en puntos de la escala de 0 a 100 de la sonda.

#### Scenario: Día objetivo sin lectura
- **WHEN** una sonda tiene humedad de las 05:00 el día D pero no el día D+3
- **THEN** no existe el ejemplo de horizonte 3 con origen D, y sí los de los demás horizontes cuyo día objetivo tenga lectura

#### Scenario: Humedad de las 05:00
- **WHEN** hay lecturas entre las 04:45 y las 05:15 UTC de un día
- **THEN** la humedad de origen de ese día es la media de su frecuencia, pasada a la escala de la sonda

### Requirement: Meteorología alineada con el periodo a predecir
Cada ejemplo de horizonte N SHALL llevar la meteorología prevista para los días de origen a origen+N−1, que son los que cubren el periodo entre las 05:00 UTC del origen y las 05:00 UTC de origen+N, tomada de la previsión archivada hecha con la antelación de cada día respecto al origen (antelación 0 para el día de origen, 1 para el siguiente, y así hasta N−1). Nunca se usa el reanálisis como previsión.

#### Scenario: Antelación correcta
- **WHEN** se construye un ejemplo con origen el lunes y horizonte 3
- **THEN** la lluvia y la ET₀ del lunes son las de la previsión con antelación 0, las del martes con antelación 1 y las del miércoles con antelación 2, y el jueves no interviene

#### Scenario: Horizonte 1
- **WHEN** se construye un ejemplo de horizonte 1 con origen el lunes
- **THEN** la meteorología es la del propio lunes con antelación 0, no la del martes

#### Scenario: Unidad asignada a su ubicación
- **WHEN** varias unidades comparten ubicación (coordenadas redondeadas a 2 decimales)
- **THEN** todas reciben la misma meteorología de esa ubicación

#### Scenario: Falta la previsión
- **WHEN** falta algún valor de la previsión de un día del periodo a predecir
- **THEN** el ejemplo conserva el valor ausente como nulo y queda marcado como «meteorología incompleta», sin rellenarlo con el reanálisis

### Requirement: Marcado de posibles riegos
El sistema SHALL marcar como posible riego cada día d en que el índice de humedad de una unidad sube más de un umbral entre las 05:00 del día d−1 y las 05:00 del día d y la lluvia de ese intervalo no lo explica. La lluvia del intervalo se aproxima con la del día d−1 con antelación 0. El umbral, la lluvia mínima y la forma de agregar las profundidades (media o cualquier profundidad) son parámetros configurables y se registran junto con la tabla.

#### Scenario: Subida sin lluvia
- **WHEN** el índice sube por encima del umbral de un día a otro y la lluvia del día anterior es inferior a la lluvia mínima configurada
- **THEN** ese día se marca como posible riego para esa unidad

#### Scenario: Subida con lluvia
- **WHEN** el índice sube por encima del umbral y la lluvia del día anterior supera la lluvia mínima
- **THEN** ese día no se marca como riego

### Requirement: Exclusión de riegos por horizonte
El sistema SHALL excluir un ejemplo de horizonte N si hay un posible riego marcado en la unidad entre el día de origen+1 y el de origen+N. No SHALL excluirlo por riegos anteriores o iguales al día de origen.

#### Scenario: Riego a 5 días
- **WHEN** hay un riego marcado en el día origen+5
- **THEN** se excluyen los ejemplos de horizonte 5, 6 y 7 de ese origen y se conservan los de 1 a 4

#### Scenario: Riego de ayer
- **WHEN** hay un riego marcado el día anterior al origen
- **THEN** el ejemplo no se excluye por ello

### Requirement: Exclusión de periodos de instalación
El sistema SHALL excluir los días de instalación de cada sonda como origen y como objetivo. Los detectará una regla automática aplicable a todas las unidades (el tramo inicial mientras el índice está por debajo de 20, antes de llegar por primera vez a un valor plausible) y una lista manual por unidad en la configuración. Con los datos del 4 de noviembre de 2026, 98 de las 128 unidades tienen valores menores de 20 en sus dos primeras semanas.

#### Scenario: Instalación de la 1168
- **WHEN** la unidad 1168 empieza el 9 de marzo de 2025 con valores de 3 a 7 y pasa a 72 el 13 de marzo
- **THEN** ningún ejemplo de la 1168 tiene origen ni objetivo en los días de instalación detectados (del 9 al 11 de marzo de 2025)

#### Scenario: Suelo muy seco después de funcionar
- **WHEN** una unidad que ya ha dado valores plausibles baja de 20 en pleno verano
- **THEN** esos días no se marcan como instalación

### Requirement: Variables de estación y estado reciente
Cada ejemplo SHALL incluir la estación del año como seno y coseno del día del año de origen, la profundidad, el índice de origen y los cambios de los 1 y 3 días anteriores (nulos si faltan).

#### Scenario: Continuidad del año
- **WHEN** se comparan ejemplos con origen el 31 de diciembre y el 1 de enero
- **THEN** sus variables de estación son casi iguales

### Requirement: Recuento de la construcción
Al construir la tabla, el sistema SHALL informar de cuántos ejemplos hay por horizonte antes y después de cada exclusión, y cuántos quedan por unidad.

#### Scenario: Informe de la construcción
- **WHEN** se ejecuta la preparación de la tabla
- **THEN** se muestra, para cada horizonte, el número de ejemplos inicial, los quitados por riego, por instalación y por meteorología incompleta, y el número final
