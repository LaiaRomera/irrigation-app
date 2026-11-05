# Spec Delta

## Purpose

Decidir de forma determinista, reproducible y auditable si hay que regar en los próximos 7 días y cuánto, aplicando FAO-56 a la humedad predicha por el modelo y a los parámetros de la parcela. Ninguna IA calcula la dosis.

## ADDED Requirements

### Requirement: Cálculo determinista y aislado
El motor SHALL ser una función pura: con las mismas entradas devuelve el mismo resultado, sin acceder a la red ni a la base de datos. Las entradas y salidas SHALL tener una estructura validada, y un valor desconocido SHALL ser nulo, nunca inventado.

#### Scenario: Mismas entradas
- **WHEN** se ejecuta el motor dos veces con las mismas entradas
- **THEN** los dos resultados son idénticos

#### Scenario: Entrada no válida
- **WHEN** una entrada tiene un tipo o un rango imposible (por ejemplo, una eficiencia de 1,4)
- **THEN** el motor rechaza la entrada con un error que nombra el campo

### Requirement: Humedad de la zona radicular
El motor SHALL combinar, para cada día, la humedad predicha de las sondas incluidas en la media de la parcela (las marcadas como tales en la unidad; en la 1168, 15, 25 y 35 cm) y pasarla a fracción de agua útil gastada con los anclajes globales: `(anclaje superior − humedad) / (anclaje superior − anclaje inferior)`. La profundidad radicular del cultivo SHALL usarse para el agua útil en mm (TAW), no para elegir las sondas.

#### Scenario: Sonda fuera de la media
- **WHEN** hay sondas a 15, 25, 35 y 55 cm y la unidad solo incluye 15, 25 y 35 cm en su media
- **THEN** la sonda de 55 cm no interviene en el cálculo

#### Scenario: Falla una sonda
- **WHEN** una de las sondas de la zona radicular no tiene predicción
- **THEN** el motor calcula con las demás y la recomendación es `provisional`, con una limitación que nombra la sonda

### Requirement: Plan de 7 días y dosis
El motor SHALL buscar el primer día del horizonte en que el agotamiento supera la fracción p y SHALL calcular para ese día la dosis neta (agotamiento × TAW × profundidad radicular), en mm, y la dosis bruta (neta / eficiencia). Solo dará el volumen en m³ si se conoce la superficie, y nunca dará una duración sin caudal.

#### Scenario: Cruce del umbral el día 4
- **WHEN** el agotamiento predicho supera p por primera vez el día 4
- **THEN** el plan recomienda regar el día 4 con su dosis neta y bruta en mm, y los días 1 a 3 «no regar»

#### Scenario: Sin superficie
- **WHEN** la parcela no tiene superficie
- **THEN** la recomendación da la dosis en mm y el volumen es nulo

#### Scenario: No se cruza el umbral
- **WHEN** ningún día del horizonte supera p
- **THEN** la decisión es «no regar» en los 7 días

### Requirement: Decisión y estado de validación
El resultado SHALL incluir, por día, una decisión (`irrigate`, `postpone`, `do_not_irrigate` o `insufficient_data`, los valores que admite `recommendation.decision`) y, para todo el cálculo, un estado de validación (`validated`, `provisional` o `insufficient_data`), los factores usados (Kc, etapa, ETc, TAW, p, anclajes) con su origen y la lista de limitaciones.

#### Scenario: Sin datos del suelo
- **WHEN** todas las predicciones de la zona radicular son `insufficient_data`
- **THEN** la decisión es `insufficient_data`, no hay dosis y el resultado explica qué falta

#### Scenario: Escala relativa
- **WHEN** la humedad viene en el índice relativo de la sonda
- **THEN** el resultado se marca como indicativo e incluye el análisis de sensibilidad de la dosis frente a los anclajes

### Requirement: Recomendación inmutable y versionada
Cada cálculo SHALL guardarse como una nueva recomendación con una copia de todas sus entradas. Un recálculo SHALL crear una versión nueva que apunte a la anterior, sin modificarla.

#### Scenario: Recalcular
- **WHEN** se pide de nuevo la recomendación de una parcela el mismo día
- **THEN** se crea una nueva fila en `recommendation` enlazada a la anterior, y la anterior no cambia
