# Spec Delta

## Purpose

Predecir la humedad del suelo de cada sonda para los 7 días siguientes, sin riego futuro, con modelos entrenados de forma reproducible, evaluados frente a la persistencia y registrados con sus métricas, para que el motor de riego reciba siempre un nivel de humedad con su calidad.

## ADDED Requirements

### Requirement: Modelos comparados
El sistema SHALL entrenar, por horizonte de 1 a 7 días, tres métodos sobre los mismos ejemplos: persistencia (cambio cero), Ridge y LightGBM. Los tres SHALL predecir el cambio del índice de humedad, y el nivel predicho SHALL ser la última humedad válida más ese cambio.

#### Scenario: Persistencia
- **WHEN** se predice con la persistencia
- **THEN** la humedad predicha en cada horizonte es igual a la de origen

#### Scenario: Nivel a partir del cambio
- **WHEN** un modelo predice un cambio de −2,5 puntos a 3 días y la humedad de origen es 70
- **THEN** la predicción entregada es 67,5

### Requirement: Validación por ubicación y fecha
La evaluación SHALL usar ubicaciones que no se vieron al entrenar y fechas posteriores a una fecha de corte, con un margen de 7 días entre el último objetivo de entrenamiento y el primer origen de validación. Las unidades que comparten ubicación SHALL ir siempre al mismo lado.

#### Scenario: Sin fuga temporal
- **WHEN** la fecha de corte es C
- **THEN** ningún ejemplo de entrenamiento tiene día objetivo posterior a C, y ningún ejemplo de validación tiene origen anterior a C + 7 días

#### Scenario: Ubicación apartada
- **WHEN** una ubicación está en el conjunto de validación
- **THEN** ninguna de sus unidades aparece en el entrenamiento

#### Scenario: Ubicación de coordenada dudosa
- **WHEN** una ubicación agrupa unidades con la coordenada por defecto de la empresa (`41.66_0.56`) y no es un solo sitio real
- **THEN** sus unidades van siempre a entrenamiento y nunca se aparta como bloque, y la evaluación se repite también sin las unidades con coordenada `shared`

### Requirement: Métricas por horizonte
El sistema SHALL calcular MAE, RMSE y sesgo de cada método por horizonte (d1 a d7), en puntos del índice, sobre los mismos ejemplos de validación, y la mejora relativa de cada método frente a la persistencia.

#### Scenario: Tabla comparativa
- **WHEN** termina la evaluación
- **THEN** se obtiene una tabla con método × horizonte × (MAE, RMSE, sesgo, número de ejemplos) y la mejora sobre la persistencia

### Requirement: Reproducibilidad del entrenamiento
Cada modelo entrenado SHALL registrarse con algoritmo, horizonte, fechas de entrenamiento, fuente de datos, unidad de la humedad, variables, hiperparámetros, métricas, ruta del archivo del modelo y versión del código. Con los mismos datos y la misma semilla, el entrenamiento SHALL dar las mismas métricas.

#### Scenario: Registro del modelo
- **WHEN** se entrena un modelo
- **THEN** existe una fila en `ml_model` con todos esos campos, y el archivo del modelo está fuera de la base de datos y fuera de git

#### Scenario: Un modelo activo por horizonte
- **WHEN** se activa un modelo para un horizonte que ya tenía uno activo
- **THEN** el anterior queda inactivo y solo hay uno activo para ese horizonte

### Requirement: Predicción con degradación por escalones
Al predecir para una sonda y un momento de origen, el sistema SHALL devolver una humedad por horizonte con su método (`model` o `persistence_fallback`), su calidad (`validated`, `provisional` o `insufficient_data`) y sus limitaciones codificadas.

#### Scenario: Todo correcto
- **WHEN** hay una lectura válida reciente y toda la meteorología necesaria
- **THEN** la predicción usa el modelo activo, con método `model` y calidad `validated`

#### Scenario: Falta meteorología
- **WHEN** hay una lectura reciente pero falta algún dato de la previsión
- **THEN** la predicción es la persistencia, con método `persistence_fallback`, calidad `provisional` y una limitación que indica la meteorología ausente

#### Scenario: Lectura antigua
- **WHEN** la última lectura válida tiene entre el umbral de frescura (36 horas como valor inicial, a ajustar con datos) y 3 días
- **THEN** la predicción es la persistencia, calidad `provisional` y una limitación `stale_reading` con su antigüedad en horas

#### Scenario: Sin lectura utilizable
- **WHEN** la última lectura válida tiene más de 3 días o no existe
- **THEN** no hay humedad predicha y la calidad es `insufficient_data`

### Requirement: Predicciones guardadas e inmutables
Cada predicción SHALL guardarse en `ml_prediction` con sensor, momento de origen, horizonte, humedad predicha, humedad base y fecha de la lectura base, método, calidad, limitaciones y modelo. Una predicción guardada no SHALL modificarse ni borrarse.

#### Scenario: Intento de modificar
- **WHEN** se intenta actualizar o borrar una fila de `ml_prediction`
- **THEN** la base de datos lo rechaza
