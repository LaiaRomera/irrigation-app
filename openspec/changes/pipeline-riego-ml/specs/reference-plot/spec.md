# Spec Delta

## Purpose

Disponer de una parcela de referencia de Agrodevices con los parámetros agronómicos mínimos y los anclajes de su escala de humedad, para que el motor de riego pueda calcular una dosis indicativa sobre datos reales.

## ADDED Requirements

### Requirement: Confirmación de la parcela de referencia
La parcela de referencia SHALL quedar confirmada solo cuando se conozcan su cultivo y la textura de su suelo. Hasta entonces, la unidad candidata (1168) SHALL constar como candidata, no como confirmada.

#### Scenario: Faltan cultivo o suelo
- **WHEN** no se conocen el cultivo o la textura de la unidad 1168
- **THEN** la parcela no se marca como confirmada y se puede sustituir por otra candidata (5271 o 1172) sin cambiar el resto del sistema

### Requirement: Parámetros mínimos de la parcela
La parcela de referencia SHALL tener obligatoriamente la textura del suelo y el cultivo. El tipo de riego, la fecha de inicio del ciclo y la superficie SHALL ser opcionales. Cada parámetro SHALL indicar su origen (dato del agricultor, de Agrodevices o supuesto declarado).

#### Scenario: Parcela completa
- **WHEN** se cargan la textura, el cultivo, el tipo de riego y la fecha de inicio del ciclo
- **THEN** la parcela queda lista para que el motor calcule la dosis sin supuestos

#### Scenario: Faltan el tipo de riego o la fecha de inicio
- **WHEN** hay textura y cultivo pero falta el tipo de riego, la fecha de inicio del ciclo o ambos
- **THEN** el motor calcula la dosis con un valor genérico declarado como supuesto (eficiencia genérica del riego, etapa del cultivo estimada), lo marca con una limitación (`assumed_irrigation_efficiency`, `assumed_crop_stage`) y el informe lo dice; el valor concreto de cada supuesto lo fija AgronomoFAO56 con su fuente y queda anotado en `decisiones.md`

#### Scenario: Falta la textura o el cultivo
- **WHEN** falta la textura o el cultivo
- **THEN** el motor no calcula dosis para esa parcela y la recomendación es cualitativa, con el motivo

### Requirement: Valores agronómicos derivados
A partir de la textura y del cultivo, el sistema SHALL obtener la capacidad de campo, el punto de marchitez, el agua útil por metro (TAW), la profundidad radicular, la fracción de agotamiento p, el Kc por etapa y la eficiencia del tipo de riego, de tablas FAO-56 con su fuente citada.

#### Scenario: Valores de tabla
- **WHEN** la parcela tiene textura franca y un cultivo de la tabla
- **THEN** cada valor derivado tiene su cifra y la referencia de la tabla de la que sale

### Requirement: Anclajes de la escala del sensor
Los anclajes SHALL ser **globales**, no uno por profundidad: un anclaje superior (suelo lleno) y uno inferior (punto de marchitez) para la media de las profundidades de la zona radicular, que son las marcadas como incluidas en la media de la unidad (en la 1168, 15, 25 y 35 cm). La fuente preferente SHALL ser la configuración de Agrodevices, convertida al índice de 0 a 100 con el rango de la unidad. Cada anclaje SHALL registrar su fuente, su método y, si salió de los datos, el periodo usado.

#### Scenario: Anclajes de Agrodevices
- **WHEN** la unidad tiene configuradas las líneas de Agrodevices
- **THEN** el anclaje superior es la línea de saturación y el inferior el punto de marchitez, convertidos al índice; se contrastan con los percentiles de la media de las profundidades y se registra la fuente `agrodevices`

#### Scenario: Anclaje con datos
- **WHEN** la unidad no tiene líneas de Agrodevices pero sí secados y recargas en su histórico
- **THEN** los anclajes salen de los percentiles de la media de las profundidades (suelo lleno: meseta del índice tras lluvia o riego fuerte; límite inferior: mínimo recurrente en periodos secos) y se registra el periodo y el método

#### Scenario: Sin periodo seco
- **WHEN** no hay líneas de Agrodevices y la unidad no tiene ningún periodo seco suficiente
- **THEN** el anclaje inferior usa la conversión genérica y la parcela lleva la limitación `generic_lower_anchor`

### Requirement: Parcela en la base de datos
La parcela de referencia, su usuario de prueba (`app_user`) y sus sensores (uno por profundidad) SHALL cargarse en la base de datos, junto con las lecturas de 15 minutos de esa unidad en `sensor_reading`, con el índice de 0 a 100 como valor, la marca de interpolada según el tipo `SMMi` y `quality_flag = 'ok'`. Los anclajes no tienen columna en la base de datos: se leen de la configuración de la parcela (`reference_plot.py`) y se copian en la recomendación.

#### Scenario: Carga
- **WHEN** se ejecuta la carga de la parcela de referencia
- **THEN** existen la parcela, su usuario propietario y un sensor por profundidad con lecturas, y repetir la carga no duplica filas
