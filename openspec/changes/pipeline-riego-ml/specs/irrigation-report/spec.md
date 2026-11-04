# Spec Delta

## Purpose

Convertir una recomendación del motor en un informe en español, con razonamiento agronómico redactado por un LLM, cifras garantizadas iguales a las del motor y un PDF con la plantilla de Agrodevices, con una plantilla de respaldo si el LLM falla.

## ADDED Requirements

### Requirement: Redacción con LLM y salida estructurada
El sistema SHALL pedir a un LLM externo, con rol de agrónomo, un texto con los campos resumen, motivo y aviso, a partir de los datos del motor, de la parcela y de la meteorología. El LLM no SHALL usar búsqueda web ni escribir HTML.

#### Scenario: Respuesta correcta
- **WHEN** el LLM responde con los tres campos
- **THEN** el informe usa ese texto y registra el generador `llm` y su versión (proveedor, modelo y versión del prompt)

#### Scenario: Respuesta sin estructura
- **WHEN** la respuesta del LLM no tiene los campos esperados
- **THEN** el informe usa la plantilla de respaldo

### Requirement: Validación de cifras
Antes de generar el PDF, el sistema SHALL comprobar que cada cifra y unidad del texto del LLM coincide con una cifra del motor o de los datos que se le pasaron, y que no aparece ninguna cifra nueva.

#### Scenario: Cifra cambiada
- **WHEN** el motor recomienda 12,4 mm y el texto dice 12 mm
- **THEN** la validación falla y el informe usa la plantilla de respaldo, registrando el motivo

#### Scenario: Cifra inventada
- **WHEN** el texto incluye un porcentaje de pérdida de rendimiento que el motor no dio
- **THEN** la validación falla y se usa la plantilla de respaldo

### Requirement: Plantilla de respaldo
El sistema SHALL poder generar siempre el informe sin LLM, con un texto de plantilla que contenga la decisión, las cifras del motor y sus limitaciones.

#### Scenario: Sin acceso a la API
- **WHEN** la API del LLM no responde o no hay clave configurada
- **THEN** se genera el informe con la plantilla, registrado con el generador `template`

### Requirement: PDF con la plantilla de Agrodevices
El sistema SHALL rellenar por código los campos de la plantilla HTML de Agrodevices (de 1 a 3 sectores) con el texto, las cifras, las gráficas de humedad pasada y prevista por profundidad, la lluvia y la ET₀ previstas, y la dosis, y SHALL exportarla a PDF con un navegador sin interfaz.

#### Scenario: Descarga del PDF
- **WHEN** se pide el PDF de un informe
- **THEN** se genera a partir del contenido guardado del informe, con la fecha de su recomendación, y no se guarda el PDF en la base de datos

#### Scenario: Recomendación indicativa o sin dosis
- **WHEN** la recomendación es indicativa o `insufficient_data`
- **THEN** el PDF lo dice de forma visible y, si no hay dosis, explica qué falta y qué hay que revisar

### Requirement: Error típico por horizonte
El informe SHALL indicar el error típico de la predicción de humedad por horizonte (de 1 a 7 días), tomado de las métricas del backtesting guardadas en `ml_model.metrics`, para comunicar la incertidumbre sin inventar intervalos.

#### Scenario: Informe con error típico
- **WHEN** se genera el informe de una recomendación basada en un modelo activo
- **THEN** el contenido y el PDF muestran el error típico de cada horizonte tal como consta en `ml_model.metrics`

### Requirement: Informe diario y semanal
El sistema SHALL poder generar el informe diario, que detalla el primer día del plan, y el semanal, que contiene ese mismo informe detallado del primer día y, para los seis días restantes, solo lo necesario para decidir: la decisión de riego, la dosis en mm si la hay y la lluvia prevista. Los dos salen de la misma recomendación, con el tipo en `report.report_type`.

#### Scenario: Informe semanal
- **WHEN** se pide el informe semanal de una recomendación
- **THEN** se guarda un informe de tipo `weekly` enlazado a esa recomendación, con el primer día detallado y los otros seis en forma resumida

#### Scenario: Informe diario
- **WHEN** se pide el informe diario de una recomendación
- **THEN** se guarda un informe de tipo `daily` con solo el primer día, detallado

### Requirement: Informe guardado
Cada informe SHALL guardarse en `report` enlazado a su recomendación, con el contenido estructurado, el texto en español, el generador y su versión. Se SHALL poder guardar varios informes de una misma recomendación.

#### Scenario: LLM y plantilla de la misma recomendación
- **WHEN** se genera un informe con el LLM y otro con la plantilla para la misma recomendación
- **THEN** existen dos filas en `report` con generadores distintos y la misma recomendación
