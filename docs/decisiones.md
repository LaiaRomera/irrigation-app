# Decisiones de arquitectura y alcance

Última actualización: 14 de octubre de 2026.

Este documento es el **diario de decisiones**: qué se acordó, qué es provisional, qué se deja para el futuro y qué falta por saber. Las decisiones acordadas prevalecen sobre documentos anteriores.

El detalle técnico vive en otros documentos:
- [arquitectura.md](arquitectura.md): procesos, flujos, módulos, componentes de IA, pantallas y seguridad.
- [base_de_datos.md](base_de_datos.md): diccionario de datos, relaciones y restricciones.
- [erd.md](erd.md): diagrama entidad-relación.
- [puesta_en_marcha.md](puesta_en_marcha.md): cómo montar el entorno.
- [glosario.md](glosario.md): equivalencias inglés–español.

## 1. Decisiones acordadas

### Decisiones del 22 de octubre de 2026 (sustituyen a lo anterior donde lo contradicen)
Origen: el documento de arquitectura del motor de cálculo (Word, v0.2) y un resumen de arquitectura general, revisados con Laia. Las demás secciones de este diario, la arquitectura y el README **aún no están actualizados** con estos cambios; se propagarán cuando Laia entregue el diseño del modelo de machine learning.

**Motor de cálculo**
- Se adopta el diseño del Word: paquete `irrigation_engine/` en Python puro, sin red ni base de datos, con entradas y salidas estructuradas (Pydantic), fórmulas de FAO-56 (ETc = ET0 × Kc, TAW, RAW, dosis neta y bruta, volumen teórico solo con superficie conocida), sin cálculo de duración sin caudal y con valores desconocidos a `null`.
- **El motor no predice la humedad del suelo: la recibe del modelo de machine learning.** Pendiente de reconciliar con las secciones 4.3 y 6 del Word, que proyectan el balance hídrico a 7 días, cuando Laia pase el diseño del ML.
- **Un solo cálculo con el plan de 7 días dentro**; el informe diario usa el primer día.
- **Versionado inmutable:** cada recálculo crea una nueva recomendación vinculada a la anterior; nunca se sobrescribe (sustituye a "una por parcela y día, que se actualiza").
- **Dos estados distintos:** la **decisión** (regar, aplazar, no regar, datos insuficientes) y el **estado de validación** (`validated`, `provisional`, `insufficient_data`).
- **Los clientes viven fuera del motor.** Flujo: el conector descarga, se normaliza y se guarda en la base de datos, y de ahí pasa al motor.
- **Riegos ejecutados: fuera**, porque no hay esa información.

**IA generativa (sustituye a lo del fine-tuning)**
- **Fine-tuning descartado.** El texto lo genera un **LLM externo mediante API**, con instrucciones fijas, ejemplos de informes (few-shot) y plantilla. Ya no hace falta crear datos de entrenamiento ni usar la GPU. Pendiente: proveedor, coste y capa gratuita (choca con "todo gratuito"), y que el informe básico de plantilla sirva de respaldo si la API falla.
- **RAG: antes entraba en el MVP; desde el 3 de noviembre de 2026 pasa a opcional** (ver «Redacción del informe» más abajo). Pendiente si se hace: qué documentos y su licencia, y el modelo de embeddings (preferible local y gratuito).
- Validación antes de generar el PDF: las cifras y unidades coinciden con el motor, están las secciones obligatorias y las fuentes citadas existen de verdad.

**Redacción del informe (decidido por Laia el 3 de noviembre de 2026)**
- **LLM externo por API con rol de agrónomo.** El prompt incluye el rol, los datos calculados por el motor, los de la parcela y la meteorología, y el formato del informe. Se le pide salida estructurada (campos: resumen, motivo, aviso) para poder validarla y maquetarla. El LLM por API no navega por internet: responde con lo aprendido en su entrenamiento (incluida la guía FAO-56). No se usará la herramienta de búsqueda web de ningún proveedor, porque el resultado no sería reproducible ni citable.
- **El valor añadido del LLM es el razonamiento agronómico:** no se limita a repetir cifras, sino que explica por qué (cultivo, etapa, método de riego, origen del Kc, previsión) y qué implica. Para que razone sobre la parcela real, el motor le pasa también los factores en los que se apoyó.
- **Regla de los tres tipos de contenido:**
  1. *Cifras y decisión* (Kc, ETc, déficit, volumen, regar o no, fechas): solo las pone el motor; el LLM las copia sin cambiarlas ni añadir otras.
  2. *Razonamiento agronómico* (por qué y qué significa): el LLM aporta su conocimiento.
  3. *Datos concretos que el motor no ha dado* (por ejemplo, porcentajes de pérdida de rendimiento): no se inventan; si se menciona el efecto, solo de forma cualitativa y sin cifras.
- **Validación por código antes del PDF:** cada cifra y unidad del texto debe coincidir con las del motor, y no pueden aparecer cifras nuevas. Si falla, se usa la plantilla de respaldo.
- **Sin fine-tuning** (ya descartado): faltan cientos de ejemplos buenos, es más caro y lento, y el modelo ya sabe agronomía; el estilo se consigue con el prompt y ejemplos. Lo único que se entrena en el proyecto es el modelo de ML de humedad (Ridge y LightGBM).
- **Sin agente en el MVP:** el flujo es fijo (leer, calcular, explicar, validar, PDF) y la recomendación debe ser reproducible y auditable; un agente añadiría imprevisibilidad, coste y dificultad para depurar. Queda como mejora futura (por ejemplo, un chat «¿por qué me dices esto?» que consulte la base de datos).
- **RAG: se decide tras comparar.** Se construye primero el flujo con rol y datos del motor. Después se comparan las explicaciones con y sin unos pocos pasajes citados de FAO-56 (cifras o afirmaciones sin respaldo, verificabilidad de las explicaciones). El RAG entra solo si mejora el resultado, y la comparación es un resultado propio del TFM. Se haría el último, tras ML, motor y PDF, y con la plantilla como respaldo.

**Tecnología**
- **Migración a PostgreSQL con pgvector** (decisión de Laia, 22 de octubre de 2026, que sustituye a "MySQL se mantiene"): la base de datos debe poder servir a futuros proyectos y tener búsqueda vectorial lista, y es mejor cambiarla ahora que está vacía. **Hecho el mismo día:** esquema y script reescritos, tablas del RAG añadidas y todo validado en Docker local. Aloja Aiven PostgreSQL (plan gratuito, con pgvector): servicio creado, esquema aplicado y las 36 pruebas repetidas allí sin fallos.
- **Se sigue con Streamlit, sin FastAPI en el MVP:** Streamlit llama a funciones del paquete. Pydantic sí.
- **PDF: ReportLab** (decidido el 22 de octubre de 2026 tras probar en el ordenador de Laia). WeasyPrint no arrancó en Windows porque necesita librerías gráficas del sistema (Pango/GTK) que no están instaladas; ReportLab, que es solo Python, generó un informe de prueba con tabla, gráfica de Matplotlib, tildes y símbolos como ET₀ y m³. Las fuentes estándar de PDF no tienen todos los símbolos, así que se usa una fuente Unicode (DejaVu, que trae Matplotlib).

### Modelo de machine learning (revisión del documento «Arquitectura ml», 22 de octubre de 2026)
- **El ML predice la humedad del suelo y el motor calcula el déficit a partir de ella.** El motor no vuelve a sumar la lluvia ni la ET₀ previstas a la humedad que recibe: el efecto de la meteorología ya va dentro de la predicción.
- **El ML vive dentro del paquete `irrigation_app`**, no como proyecto aparte.
- **Modelos: persistencia (referencia), Ridge y LightGBM**, con un único flujo de entrenamiento reproducible. Quedan fuera XGBoost, LSTM y la monitorización de deriva (mejora futura).
- **Hora de origen de la predicción: 05:00 UTC**, alineada con la ingesta diaria y con la ventana de 24 h del Gold.
- **Métricas: MAE, RMSE y sesgo por horizonte (d1 a d7)**, comparadas con la persistencia sobre las mismas fechas. Sobre la humedad; cruzar umbrales de riego es cosa del motor.
- **Salida: un valor de humedad por sensor y horizonte**, sin intervalos en el MVP. La incertidumbre se comunica con el error típico por horizonte medido en el backtesting.
- **Los sensores están a varias profundidades**; una profundidad por sensor, no un modelo por profundidad (por decidir con los datos reales).
- **[CORREGIDO el 3 de noviembre de 2026 con el CSV real: son 190 unidades y 2,7 millones de filas; ver «Datos reales de Agrodevices recibidos»]** Datos disponibles según se dijo antes: 71 unidades y unos 194.000 registros (unas 4 semanas por unidad).
- **Sin registros de riego:** no habrá riegos programados. **Decidido por Laia el 23 de octubre de 2026:** el modelo debe predecir la humedad *sin riego futuro*, porque el motor es quien decide cuánto regar. Se marcan con una regla los posibles riegos (subida de humedad sin lluvia) y se **excluyen del entrenamiento los ejemplos cuyo periodo a predecir contiene un riego marcado**. Los riegos pasados no se excluyen: el estado de partida del suelo es el real, aunque ayer se regara. Se aplica por horizonte (un riego marcado a los 5 días no invalida el ejemplo para d1 a d4).
- **El modelo predice el cambio de humedad** (humedad en el día d menos la última humedad válida) **y el módulo de ML lo suma a la última lectura**: el motor recibe siempre un nivel de humedad, nunca un cambio. Decidido por Laia el 23 de octubre de 2026. La persistencia equivale a predecir cambio cero. Si no hay una lectura reciente válida, no hay base para sumar y la predicción es `insufficient_data`.
- **Un modelo global, no uno por parcela:** se entrena con todos los sensores y parcelas a la vez, con variables que describen a cada uno (profundidad, comportamiento reciente...), para que una parcela nueva funcione desde el primer día. Se comprobará dejando sensores o parcelas fuera del entrenamiento.
- **El motor combina las profundidades** de los sensores dentro de la zona radicular para obtener una humedad por día (decidido el 23 de octubre de 2026): la profundidad radicular es un parámetro agronómico y por tanto del motor. El ML entrega una predicción por sensor.
- **Criterio de riego del MVP: genérico**, que mantiene la humedad dentro del rango útil (entre la capacidad de campo y el umbral de agotamiento admisible), sin objetivos distintos por cultivo. Con la predicción «sin riego», el motor busca el primer día en que la humedad cruza el umbral y calcula la dosis para volver a la capacidad de campo. Objetivos por cultivo (más seco o más húmedo según el cultivo): mejora futura, a estudiar.
- **Las predicciones de humedad se guardan** (decidido por Laia el 23 de octubre de 2026), en dos tablas: `ml_model` (una fila por modelo entrenado) y `ml_prediction` (una fila por sensor, momento de origen y horizonte; inmutable, como `recommendation`). Los archivos de los modelos van fuera de la base de datos. Justificación:
  - **Reproducir una recomendación no lo exige**: `recommendation.inputs_snapshot` ya copia las predicciones usadas. No es la razón principal.
  - **Medir el modelo en producción.** Pasados los días del horizonte se conoce la humedad real y se puede comparar con lo que el modelo dijo entonces. Ese error real es la mejor evidencia de que el modelo funciona y alimenta el «error típico por horizonte» de los informes. Sin guardar las predicciones se pierde, y recalcularlas a posteriori es costoso y delicado.
  - **Desacoplar la web del modelo.** El ML se ejecuta en la tarea diaria de las 05:00 y deja las predicciones en la base de datos; al pedir un informe, el motor las lee. Es coherente con la regla de que web y tareas solo se comunican por la base de datos, y evita cargar LightGBM en la web de Streamlit Community Cloud.
  - **Depuración y auditoría**: permite ver qué dijo el modelo y con qué versión cuando una recomendación parece rara.
  - **Coste bajo (estimación, sin medir):** 71 sensores × 7 horizontes son unas 500 filas al día, unos 180.000 al año; decenas de MB al año frente a 1 GB gratuito. Lo que más espacio ocupará son las lecturas de sensores a 15 minutos.
  - **Riesgo de no guardarlas:** no es de corrección, sino de perder datos de evaluación y de depuración.
  - **Columnas de `ml_prediction`** (decidido por Laia el 3 de noviembre de 2026): `sensor_id`, `origin_at`, `horizon_days` (1 a 7), `target_date` (columna generada: `origin_at` + horizonte), `moisture_pred` (ya sumada a la última lectura), `base_moisture` y `base_reading_at` (última lectura válida usada como base y su fecha, para auditar y calcular su antigüedad aunque la lectura se corrija después), `method`, `quality`, `limitations`, `model_id` (referencia a `ml_model`) y `created_at`. La unidad de la humedad no tiene columna propia: se anota en `ml_model` (unidad con la que se entrenó), a falta de confirmarla con Agrodevices.
  - **`limitations` es una columna JSONB con códigos y parámetros, no una tabla aparte ni texto libre** (por ejemplo, `[{"code": "stale_reading", "age_hours": 50}]`). Hay pocas por fila y se leen siempre con la predicción; los códigos son estables al cambiar la redacción, se validan y se cuentan fácilmente, y el texto en español lo genera el informe a partir del código. El motor recoge las de las predicciones que usa y las copia a la recomendación.
  - **Columnas de `ml_model`** (decidido por Laia el 3 de noviembre de 2026; una fila por modelo entrenado y horizonte): `id`, `algorithm` (`ridge`, `lightgbm` o `persistence`), `horizon_days`, `trained_at`, `train_from` y `train_to`, `data_source` (`agrodevices`, `xms_cat` o mezcla), `moisture_unit` (pendiente de confirmar con Agrodevices), `features` (JSONB), `hyperparameters` (JSONB), `metrics` (JSONB: MAE, RMSE y sesgo del backtesting junto con la persistencia; de aquí sale el error típico del informe), `artifact_path` (archivo del modelo, fuera de la base de datos), `code_version` (versión del código o commit, para reproducir) e `is_active`. **Solo un modelo activo por horizonte**, garantizado con un índice único parcial. `artifact_path` se deja y dónde viven los archivos se decide cuando se decida dónde corre la tarea diaria (no van en el repositorio por su tamaño).
  - **No se guarda la humedad real en `ml_prediction`:** el informe no la usa (usa el error típico por horizonte del backtesting, guardado en `ml_model`). El error en producción se calcula cuando haga falta con un `JOIN` contra `sensor_reading` por sensor y `target_date`. Así no se duplica un dato que la empresa puede corregir al limpiarlo y la tabla es inmutable de verdad.
- **El motor nunca se detiene por un hueco del ML: degradación en escalones** (decidido por Laia el 23 de octubre de 2026). El relleno lo hace el módulo de ML, no el motor, para que el contrato no cambie: el motor siempre recibe un nivel de humedad, con su estado de calidad y su método (`model` o `persistence_fallback`, columna de `ml_prediction`).
  1. Modelo y datos correctos: `validated`.
  2. El modelo no puede predecir pero hay una lectura reciente (por ejemplo, falta algún dato de la previsión): **persistencia** (última humedad válida, cambio cero), `provisional`, con aviso.
  3. La última lectura tiene hasta **3 días** de antigüedad (valor inicial, a ajustar con datos reales): persistencia, `provisional`, con aviso de la edad de la lectura en `limitations`.
  4. Sin ninguna lectura utilizable (más de 3 días, sin sensores o unidad desconocida): `insufficient_data`. El motor sigue funcionando, pero **sin dosis**: sin ningún dato del suelo cualquier cifra sería inventada. El informe explica por qué no puede recomendar, qué se sabe (meteorología, última lectura con su fecha) y qué hay que revisar.
  - **Sensores que fallan:** si falla alguno de los sensores de una parcela, el motor calcula con los demás y marca la recomendación `provisional`, con aviso.
- **Cuándo una predicción es `provisional`** (decidido por Laia el 23 de octubre de 2026): cuando la lectura de partida es de hace más de unas horas (umbral por fijar con datos reales), cuando falta algún dato meteorológico necesario, o cuando el modelo se usa fuera de lo visto en el entrenamiento (por ejemplo, una profundidad que no estaba). **Una lectura interpolada no la hace provisional**: la empresa interpola unos 130 registros al mes de forma habitual. Las previsiones de Open-Meteo no deberían faltar, pero si falta alguna se trata como dato meteorológico ausente.
- **Fuente pública elegida: XMS-CAT (ICGC, Cataluña), vía ISMN** (decidido por Laia el 30 de octubre de 2026). Motivos: es de España, 22 estaciones en viñedo, varias profundidades (5 a 100 cm), y la página de ISMN la da disponible hasta 2026, lo que cubre el periodo de las previsiones archivadas (desde enero de 2024). Descartadas USCRN (EE. UU.), VAS (hasta 2012) e IPE (hasta 2020); REMEDHUS (solo 0-5 cm, hasta enero de 2025) queda como complemento opcional. La meteorología sigue viniendo siempre de Open-Meteo, en las coordenadas de cada estación. **A verificar al descargar:** fechas reales, si los viñedos se riegan, y la licencia: ISMN exige registro, prohíbe redistribuir los datos y obliga a citar la red; los datos van a `data/`, fuera de git. La descarga la hace Laia con su cuenta de ISMN.
- **Base del TFM: XMS-CAT; Agrodevices, caso adicional** (decidido por Laia el 3 de noviembre de 2026; **sustituye al «Plan B» de abajo**, que trataba los datos públicos como alternativa). Motivos: quedan unas 6 semanas; los datos de Agrodevices tienen incertidumbres que no se controlan (limpieza pendiente, sin calibración, datos anónimos sin información de la parcela); XMS-CAT está en m³/m³, con coordenadas y probablemente textura, y es citable y reproducible; así el motor se demuestra con FAO-56 sin supuestos de escala. Agrodevices **no se descarta**: el diseño ya admite un traductor por fuente a un formato interno común y un modelo por fuente (`data_source` en `ml_model`), de modo que se suma como segundo caso real, con sus limitaciones declaradas, si llegan datos buenos a tiempo. **Regla de decisión:** tras descargar XMS-CAT se comprueba que tenga datos desde 2024 (previsiones archivadas), pocos huecos, profundidades útiles y, a ser posible, textura; si lo cumple, queda confirmada; si no, se replantea. **Riesgo a mirar:** los viñedos pueden ser de secano o con riegos desconocidos; la regla de excluir posibles riegos se aplica igual.
- **Mismo código y formato interno para todas las fuentes; por defecto, un modelo por fuente** (matizado el 3 de noviembre de 2026): cada fuente tiene su traductor a un formato interno común, y el flujo de entrenamiento, backtesting y predicción es el mismo. Pero XMS-CAT está en humedad volumétrica (m³/m³) y Agrodevices en una escala relativa del sensor, así que un cambio en una no equivale a un cambio en la otra y, por defecto, cada modelo se usa solo con datos de su fuente. **Un modelo conjunto no se descarta:** consistiría en normalizar las series (por ejemplo, a una escala de 0 a 1 según el rango de cada sensor) y comprobar que funciona; se prueba como experimento y, si funciona, se adopta. Convertir Agrodevices a m³/m³ exigiría la calibración.
- **Sensores de Agrodevices: escala y entrega** (aclarado por Agrodevices, 3 de noviembre de 2026): se recibirá la **frecuencia en bruto**. El porcentaje de Agrodevices es un reescalado lineal entre 6900 y 11700, valores **elegidos por ellos** y válidos para todos los sensores según Agrodevices (**el CSV real lo contradice en parte: ver «Datos reales de Agrodevices recibidos»**; no son lecturas en aire y en agua), con la relación inversa (frecuencia alta, porcentaje bajo). El 0 % y el 100 % no tienen significado físico y **no es humedad volumétrica**. Como recibimos la frecuencia, no se necesita su fórmula del porcentaje. La calibración a humedad volumétrica no se conoce (se pregunta al fabricante) y **no habrá comprobaciones de campo**.
- **Cálculo de la dosis con la escala relativa del sensor** (propuesto el 3 de noviembre de 2026): la escala del sensor da una **fracción** de agua útil gastada, que no depende de las unidades: `(capacidad de campo − humedad) / (capacidad de campo − límite)`, todo en la escala del sensor. Los **milímetros** salen de multiplicar esa fracción por el agua útil del suelo (TAW), calculada con la textura y la profundidad radicular según FAO-56, y de dividir por la eficiencia de riego. **Anclaje superior (suelo lleno):** la meseta del sensor tras lluvia o riego fuerte. **Anclaje inferior (límite):** no se observa por sí solo, porque en una parcela regada el suelo rara vez llega ahí; se toma del mínimo recurrente en periodos secos, o de una conversión genérica aproximada si no lo hay. **Supuestos que se declaran en el TFM:** respuesta aproximadamente lineal del sensor entre ambos anclajes y la elección del anclaje inferior; se acompañan de un **análisis de sensibilidad** (cuánto cambia la dosis si se desvían). Las recomendaciones con esta escala se marcan como **indicativas**. Con XMS-CAT (m³/m³) la humedad se compara directamente con la capacidad de campo y el punto de marchitez de las tablas FAO-56, así que es el caso de referencia limpio.
- **Unidad de la recomendación:** **milímetros de agua (mm, equivalentes a L/m²)**; **m³** solo si se conoce la superficie (1 mm = 10 m³/ha); sin duración sin caudal. La humedad en % del sensor solo se muestra como información y en las gráficas.
- **Parámetros mínimos por parcela** (decidido por Laia el 3 de noviembre de 2026): **textura del suelo** (capacidad de campo, punto de marchitez y TAW por metro), **cultivo** (profundidad radicular Zr, fracción de agotamiento p y Kc) y **tipo de riego** (eficiencia Ea); **superficie opcional**. El resto se generaliza cuando sea posible y coherente, o se calcula cuando haga falta. La **etapa del cultivo** (que afecta al Kc) se estima con un calendario genérico por cultivo y la fecha, y se marca como estimación en `limitations` (a estudiar).
- **Los datos de Agrodevices llegarán con un código por unidad y sin información de la parcela.** Se trabajará sobre una sola parcela de referencia, de la que se obtendrán textura, superficie y demás. **Corrección del 3 de noviembre de 2026 con el CSV real:** el archivo **no trae coordenadas, ni profundidades, ni nombre de parcela, ni cultivo**; solo `UnitID` y `ProjectID`, y cinco canales `Frec_1` a `Frec_5` sin decir a qué profundidad está cada uno. Hace falta una tabla de unidades de Agrodevices (ver «Datos reales de Agrodevices recibidos»). Con las coordenadas se pediría la meteorología a Open-Meteo para cada unidad.
- **[AMPLIADA el 3 de noviembre de 2026 con el CSV real: ver «Datos reales de Agrodevices recibidos»]** **Pregunta a Agrodevices** (correo al responsable, 3 de noviembre de 2026; pendiente de respuesta): modelo del sensor y ficha técnica, por si hay una ecuación o curva de calibración a humedad volumétrica. **Preguntas descartadas por Laia el mismo día**, para no complicar el MVP: ubicación (los datos ya traen las coordenadas), qué parcelas se riegan y con qué sistema (no es necesario), cómo se marca lo interpolado (ya viene en una columna de los datos) y fechas de reinstalaciones. **Se trabaja con los datos que hay hoy:** los valores erróneos los marca Agrodevices con una simple comprobación de rangos, y el MVP no incluye detección de anomalías ni auditoría de la calidad de los datos de los sensores.
- **[SUSTITUIDO el 3 de noviembre de 2026 por «Base del TFM: XMS-CAT»] Plan B si los datos de Agrodevices no bastan:** complementar con conjuntos públicos de sensores de humedad del suelo, siempre validando sobre datos de Agrodevices. Pendiente de verificar fuentes, licencias y que cubran fechas con previsiones archivadas (Previous Runs solo existe desde enero de 2024).

### Alcance del TFM y componentes de IA
- **Requisito:** el TFM debe aplicar lo aprendido en el máster (machine learning, deep learning e IA generativa). El documento original de definición del TFM **ya no manda en el alcance**; el resto de decisiones (arquitectura, base de datos, usuarios) se mantiene.
- **Tres capas:** el modelo de ML/DL **predice**, las reglas del motor determinista **deciden** y un modelo de lenguaje **explica**. Ninguna IA calcula la dosis de riego: no sería reproducible ni auditable, consumiría más tokens, y no hay ejemplos de la dosis correcta con los que entrenar un modelo supervisado.
- **Modelo principal (B): previsión de la humedad del suelo con machine learning clásico** (decisión de Laia, 17 de octubre de 2026; el deep learning se aplica en el modelo de lenguaje; algoritmo concreto por decidir, candidatos *gradient boosting* o regresión regularizada), a partir de las lecturas de los sensores y de la meteorología. Permite recomendar riegos de días futuros (informe semanal). Se evalúa contra baselines: persistencia, regresión sencilla y balance de agua determinista. Datos: unos 25.000 registros de Agrodevices, de varios sensores y parcelas, que Laia puede usar (pendiente de verificar: número de sensores y parcelas, frecuencia de medida y rango de fechas).
- **Corrección del sesgo de la previsión meteorológica (A): opcional**, solo si da tiempo. Su valor depende del horizonte: a un día vista el error probablemente no cambie la dosis, pero sí puede importar en los días 3 a 7 del informe semanal. Habría que medir cuánto cambia la dosis recomendada al corregir.
- **[SUSTITUIDO el 22 de octubre de 2026: ver la sección anterior] IA generativa: fine-tuning de un modelo de lenguaje abierto y pequeño** en la GPU local (QLoRA), **integrado en el código y automático**: tras el cálculo del motor, una función genera la explicación sin intervención de nadie. Es el único uso aceptado de un LLM en el proyecto.
- **Datos de entrenamiento del LLM** (pares resultado del motor → explicación en español): **generación programática más generación con ayuda de una IA, revisando una muestra**. Debe declararse en la memoria. Idealmente, con apoyo agronómico en la revisión.
- **Fidelidad de las cifras:** el modelo genera el texto con huecos que el código rellena con las cifras del motor, y una comprobación automática compara el texto con las cifras. Si falla, se usa la plantilla, que se mantiene como respaldo.
- **Evaluación del LLM:** modelo ajustado frente a la plantilla y frente al mismo modelo sin ajustar, con casos reservados solo para evaluar; fidelidad de cifras, unidades, motivos y comunicación de la información faltante.
- **Entregable del MVP:** debe poder **generar una recomendación de riego en PDF, con gráficas y explicaciones en lenguaje natural** (decisión de Laia, 17 de octubre de 2026). El texto lo redacta el modelo de lenguaje ajustado, con la plantilla como respaldo; las gráficas las genera la aplicación a partir de los datos y las predicciones. El PDF no se guarda en la base de datos: se genera al pedirlo a partir de `report.content` y `report.text_es` (propuesta; a confirmar).
- **Recursos:** todo **gratuito y en el ordenador de Laia**: Intel Core i7-11370H, 16 GB de RAM y GPU NVIDIA GeForce de 8 GB (además de una Intel Iris). Plazo: **dos meses**. El plan semanal se hace cuando esté todo claro.

### Datos reales de Agrodevices recibidos (3 de noviembre de 2026)
Origen: `datos_limpios_202611031026.csv` (461 MB, datos «limpios» de la empresa) y un export de la unidad 1130 (`Paraguaya Flat Candy 2`, del 23 al 31 de octubre de 2026). Análisis hecho por Claude con scripts temporales; **los archivos no se suben al repositorio** (datos de la empresa; el repositorio forma parte del portfolio de Laia).

**Qué hay**
- **2.737.152 filas, 190 unidades, del 1 de junio de 2024 al 2 de noviembre de 2026**, lecturas cada 15 minutos. 185 unidades son del proyecto 30001 y 5 del 20001 (1121, 1125, 1130, 1132 y 1137). Las cifras de 71 unidades y 194.000 registros que se manejaban antes no corresponden a este archivo.
- **Histórico por unidad muy desigual:** mediana de 138 días; 35 unidades con más de 365 días, 72 con más de 180, 119 con más de 90 y 57 con menos de 30 (sobre todo pruebas de dos días de mayo de 2026). La mayoría de las series largas empiezan en 2025. Todo el periodo está cubierto por las previsiones archivadas de Open-Meteo (desde enero de 2024).
- **Columnas útiles:** `UnitID`, `fecha`, `Type`, `Frec_1` a `Frec_5` (frecuencia por canal) y `AverageFrec`. La media es la de los canales distintos de cero (comprobado con la unidad 1130).
- **Tipos de lectura (`Type`):** `SMM` 76,8 % (lectura real); `SMMi` 16,6 % (interpolada, unas 480 por unidad y mes; el 99 % son rachas de una hora o menos, la más larga de 196 filas); `SMM0` 4,8 % (todos los canales a 0: sin datos, en rachas de un día o más); `SMMAdmin` 1,8 % (107 unidades, rachas largas con valores reales; **significado sin confirmar**).
- **Huecos:** no quedan huecos cortos, porque la limpieza los rellena o los marca como `SMM0`/`SMMi`. Quedan **189 huecos de más de un día en 90 unidades** (mediana de 10 días, el 10 % pasa de 113 y el máximo es de 399). Cobertura global sobre la rejilla de 15 minutos: 79,7 %.
- **Canales:** por unidad hay entre 0 y 5 canales con datos: 107 unidades con 4, 44 con 3, 19 con 2, 9 con 5, 2 con 1 y 9 con ninguno. **El canal 4 está muerto en 156 unidades**, el 2 en 58, el 1 en 23, el 5 en 11 y el 3 en 2. Los canales sin datos valen 0 (no nulo), así que **0 hay que tratarlo como dato ausente**.
- **Hora:** `fecha` es una rejilla continua de 15 minutos, sin duplicados ni saltos en los cambios de hora: **UTC** (el export de la unidad 1130 está en hora local de Madrid y casa con el CSV desplazado 2 h antes del 25 de octubre y 1 h después). Pendiente de confirmar con Agrodevices.
- **Sin datos de riego:** `isIrrigating` vale siempre 0 y `irrigationVolume` está vacío. Se confirma que **no hay registros de riego**.
- **Lluvia y temperatura ambiente:** `rain` y `TempAmb` existen solo en 64 unidades (unas 125.000 filas), y la lluvia es distinta de 0 en solo 2 unidades. `Presion` está vacía, `Temp` es siempre 0 y `Tsen1` a `Tsen5` son enteros de 0 a 127 sin unidad conocida. La meteorología seguirá viniendo de Open-Meteo.

**Calidad: lo que se ha visto**
- **Los porcentajes de Agrodevices no son fiables como escala común.** Reproducen la fórmula lineal entre 6900 y 11700 en el 96 % de las filas, pero 87.440 filas (3,9 %) difieren más de un punto (52.677 más de cinco): por ejemplo, la unidad 1158 en agosto de 2024 sigue una escala de 6900 a 9000. **Se usará siempre la frecuencia en bruto y se ignorarán las columnas de porcentaje.**
- **Valores fuera de rango:** ninguno por encima de 11700; unos pocos por debajo de 6900 (956 en el canal 3, 171 en el 5, 54 en la media).
- **Saltos bruscos:** 1.936 lecturas con un cambio de la media de más de 200 unidades entre dos lecturas seguidas (1.024 de más de 500), en parte porque un canal que se cae a 0 cambia la media. Recalcular la media solo con canales válidos.
- **Series casi planas o congeladas:** el 65 % de las lecturas es igual a la anterior (mediana por unidad) y 25 unidades superan el 80 %. La unidad 1121 apenas se mueve en todo 2025 (67 a 72 % en la escala de Agrodevices) y desde julio de 2026 está entre 80 y 98 %: **las series no son estacionarias** y un cambio de nivel puede ser una reinstalación.
- **Rango útil:** entre las 69 unidades con 180 días o más de lecturas reales, la mediana del rango entre los percentiles 1 y 99 de la media es de 1.142 unidades de frecuencia, sobre las 4.800 de la escala.

**Tratamiento mínimo técnico acordado para el MVP** (no es detección de anomalías, que sigue fuera): tratar 0 como dato ausente; excluir las filas `SMM0`; recalcular la media con los canales válidos; tratar las `SMMi` como dato real (decisión de Laia del 3-11-2026: están en el 87 % de los días, filtrarlas dejaría casi sin datos; se conserva la marca por si hace falta analizarlo); usar solo la frecuencia; agregar a valores horarios o diarios para el modelo. `SMMAdmin` queda pendiente hasta saber qué es.

**Almacenamiento:** 2,7 millones de filas a 15 minutos **no caben de forma razonable en el gigabyte gratuito de Aiven** junto con el resto del sistema. Los datos de entrenamiento se quedan en archivos locales o en el PostgreSQL de Docker, y a Aiven irán solo los datos operativos (agregados) de las parcelas que se usen.

**Lo que falta y se debe pedir a Agrodevices** (por orden de importancia)
1. **Tabla de unidades:** coordenadas aproximadas, profundidad de cada canal (1 a 5), nombre de parcela, cultivo y tipo de riego. Sin esto no se puede asociar la meteorología a cada unidad ni combinar canales en la zona radicular. **Es el bloqueo principal.**
2. Significado exacto de `SMM0`, `SMMAdmin` y `SMMi`, y qué regla de limpieza usan.
3. Confirmación de que `fecha` está en UTC.
4. `SMMMin` y `SMMMax` por unidad y periodo (el export de la 1130 los trae en la cabecera; los datos muestran que no todas las unidades usan 6900 a 11700).
5. Modelo del sensor y ficha técnica o curva de calibración, por si existe (la pregunta del correo anterior).
6. Unidades y significado de `Tsen`, `rain` y `TempAmb`.
7. Opcional: fechas de reinstalaciones o cambios de sonda, y qué parcelas se riegan y cómo.

**Efecto sobre la decisión de base (XMS-CAT):** con 35 unidades de más de un año, los datos de Agrodevices parecen suficientes para entrenar, así que la base del TFM puede acabar siendo Agrodevices, con XMS-CAT como caso de referencia limpio en m³/m³. La decisión se reevalúa cuando llegue la tabla de unidades: si trae coordenadas y profundidades, Agrodevices pasa a ser la fuente principal; si no, sigue XMS-CAT. Mientras tanto, la descarga de XMS-CAT sigue en pie.

### Limpieza de unidades de Agrodevices: de 481 a 128 (3 de noviembre de 2026)
Tablas de partida: `player_data` (dispositivos: modelo, coordenadas, activo) y `profiler_setup` (profundidades y escala de cada sonda; `UnitID` de esta tabla es el `Device_ID` de la otra, y se renombra a `unit_id`). Código: `src/irrigation_app/sources/agrodevices.py`, con un filtro por función y el registro de lo que quita cada uno; se ejecuta con `scripts/prepare_agrodevices_units.py` y guarda `data/agrodevices/units.parquet` (fuera de git). Cubierto por 7 pruebas con datos sintéticos.

| Filtro | Antes | Después | Por qué |
|---|---|---|---|
| Modelo `SMM` | 481 | 262 | Solo las sondas de humedad; el resto de modelos de `player_data` no mide humedad del suelo. |
| Activa (`Activo = 1`) | 262 | 245 | Según Laia, las unidades con `Activo = 0` no envían datos. |
| No es demo | 245 | 243 | Las unidades 2000 y 2001 son demostraciones de la empresa (indicado por Laia). |
| Tiene lecturas | 243 | 176 | Hay unidades dadas de alta que no aparecen en el CSV de lecturas. |
| Tiene configuración de sonda | 176 | 176 | No quita ninguna. |
| Tiene alguna profundidad | 176 | 176 | No quita ninguna. |
| Tiene coordenadas reales | 176 | **128** | Sin ubicación no se puede pedir la meteorología. Se quitan 48 unidades con la coordenada por defecto de la plataforma (Armenia, 44,519 / 40,196; proyectos «Project N») o con 0, 0. |

**Decisiones**
- Se conserva el tipo de lectura `SMMi` (interpolada): el filtro por `SMM` es solo sobre el **modelo** de `player_data`, no sobre el `Type` de las lecturas.
- Las **profundidades** salen de `Levels` (cm): se usan los 5 primeros valores y 0 significa «sin sensor». Esas profundidades y el rango `SMMMin`/`SMMMax` se guardan **por unidad** (no es 6900 a 11700 para todas).
- Si una unidad aparece dos veces en `profiler_setup`, se queda la fila de mayor `id` (la más reciente).
- Resultado: **128 unidades**, con 4 profundidades en 107, 5 en 9 y 3 en 12.
- Las coordenadas se marcan en `coord_status`: `ok` (103) y `shared` (25). `shared` es una misma ubicación (a unos 100 m) con clientes distintos: puede ser la misma finca dada de alta dos veces o una coordenada por defecto de la empresa (41,656 / 0,557, que aparece en varias fincas). **Se confirmará con Agrodevices; mientras tanto se conservan.**
- Esta limpieza solo decide qué unidades hay. Las lecturas se traducen aparte (siguiente apartado).

**Tabla de lecturas** (`read_readings` en el mismo módulo; `scripts/prepare_agrodevices_readings.py`; guarda `data/agrodevices/readings.parquet`, fuera de git; 5 pruebas)
- Formato largo: una fila por **unidad, instante y profundidad**, con `unit_id`, `timestamp` (UTC), `depth_cm`, `frequency` (entero, solo la frecuencia en bruto) y `reading_type`. Los canales se traducen a profundidad con la configuración de la sonda.
- Un **0 en un canal es dato ausente** y no se guarda; tampoco los canales sin profundidad configurada.
- Se **excluyen las filas `SMM0`** (todos los canales a 0). Se conservan, marcadas en `reading_type`, `SMMi` (interpolada) y `SMMAdmin` (significado sin confirmar; quitarla es filtrar por ese tipo).
- No se recalcula la media de la sonda: se hará al agregar, con los canales que entran en la media (`in_average_N`).
- Resultado: de 2.737.152 filas se descartan 194.880 (unidades fuera de la tabla) y 103.062 (`SMM0`); quedan 2.439.210 filas y **9.522.149 lecturas** (`SMM` 7,70 M, `SMMi` 1,67 M, `SMMAdmin` 0,15 M) en 128 unidades, del 1 de junio de 2024 al 2 de noviembre de 2026, sin duplicados. Se descartan 2.673.901 valores (canal a 0 o sin profundidad).
- **Pendiente:** la frecuencia mínima es 6.501, por debajo del rango de la escala (6.900). No se corrige ahora (no hay detección de anomalías en el MVP); se tendrá en cuenta al modelar.

**Agregación diaria** (`aggregate_daily` en `src/irrigation_app/aggregation.py`, independiente de la fuente; `scripts/prepare_daily_readings.py`; guarda `data/agrodevices/daily.parquet`, fuera de git; 3 pruebas)
- Una fila por **unidad, día y profundidad**, con media, mínimo y máximo de la frecuencia del día y el número de lecturas (total, reales `SMM`, interpoladas `SMMi` y `SMMAdmin`). No se descarta ningún día: los recuentos permiten decidirlo después.
- **El día es el día UTC**, coherente con `fecha`. La diferencia con la hora de Madrid es de 1 o 2 horas.
- **Las lecturas interpoladas (`SMMi`) se tratan como dato real.** Aparecen en el 86,7 % de los días (unas 16 al día), así que excluirlas o penalizarlas dejaría el conjunto casi vacío. Los recuentos se conservan por si más adelante se quiere analizar su efecto.
- Resultado: 9.522.149 lecturas pasan a **100.168 valores diarios** en 128 unidades. El 98,7 % de los valores tiene las 96 lecturas del día y solo el 1,2 % tiene menos de 48; el 1,5 % no tiene ninguna lectura `SMM` real. Días con datos por unidad: mediana de 152; 56 unidades con 180 o más, 21 con 365 o más y 9 con menos de 30.

**Escala común entre sondas** (`add_relative_moisture` en `src/irrigation_app/scale.py`; se aplica en `prepare_daily_readings.py`, que añade las columnas a `daily.parquet`; 5 pruebas)
- Índice de humedad relativa de 0 a 100 por unidad: `(smm_max − frecuencia) / (smm_max − smm_min) × 100`, con el rango de cada sonda (`smm_min`, `smm_max`). Es la misma fórmula lineal que usa Agrodevices. **0 es el extremo seco de la escala de esa sonda y 100 el húmedo** (frecuencia alta = suelo seco).
- Columnas: `moisture_index_mean`, `moisture_index_min` (sale de la frecuencia máxima del día) y `moisture_index_max` (de la mínima).
- **No es humedad volumétrica** ni hay calibración de suelo: es un índice relativo al rango de la sonda, y la **linealidad es una suposición declarada** (con el análisis de sensibilidad previsto). No se recortan los valores fuera de 0 a 100; una unidad sin rango válido queda con nulos.
- Resultado: sin nulos y todos los valores diarios medios dentro de 0 a 100 (media 69, mediana 72,5). Las referencias de campo y el punto de marchitez salen de los datos de cada sonda, no de este índice.

### Informe actual de Agrodevices (prompt, especificación y plantilla; 3 de noviembre de 2026)
Documentos recibidos: `00_Prompt_HTML.txt`, `00_AGRODEVICES_MACHINE_SPEC.docx` (revisión 37), `02_Documentacion_estructura.docx`, `03_Plantilla_informe.pptx` y `Agrodevices-.zip` (plantilla `MASTER_HTML` con `index.html`, `agrodevices.css` y 34 recursos, más `01_Documentacion_variables_informe.docx`). **Son documentos internos de la empresa: no se suben al repositorio; aquí solo se resumen.** Se usan para saber cómo quieren el informe; la salida final no se rediseña, solo se mejora su calidad si hace falta.

**Cómo funciona hoy**
- **Entradas:** por cada sonda, dos gráficas (`depth`, humedad por profundidad, y `average`, media con bandas de zona de manejo) y un documento de datos manuales con el cliente, la localidad y el tipo de sonda, y, **por sector**, cultivo, variedad, destino del cultivo, fecha de siembra, fase fenológica, fecha prevista de cosecha, densidad, sistema de plantación, tipo de riego, tasa de riego, observación de la sonda y observaciones adicionales. No leen los datos en bruto: «los datos en bruto de los sensores no son obligatorios».
- **Flujo:** un LLM ejecuta una comprobación previa (PRECHECK) que devuelve cuatro bloques y un resultado (`APTO PARA GENERAR` o `REQUIERE REVISIÓN`); con la orden `GENERAR` rellena una plantilla HTML. Máximo de 15 sectores por informe (se parte en varios). El PDF, en una fase posterior, se renderiza desde ese HTML con Chromium; la especificación prohíbe reconstruirlo con ReportLab o WeasyPrint.
- **Páginas del informe:** portada, propósito (cuatro objetivos), contexto (tarjetas por sonda: id, parcela, profundidades, cultivo, riego y estado), metodología (fija: patrón «diente de sierra» y bandas de color azul, verde claro, verde, amarillo y rojo), análisis por sector (las dos gráficas y tres observaciones), comparativa (o diagnóstico si hay un solo sector), previsión de 7 días con recomendación semanal por sector, cuadro de mandos (aprovechamiento del agua, profundidad radicular activa, riesgo de estrés y riesgo de percolación), síntesis, valor y cierre.
- **Previsión meteorológica:** AEMET con Open-Meteo de respaldo; los días con máxima superior a 37 °C se marcan en rojo (las dos máximas distintas más altas, con empates).
- **Reglas que coinciden con las nuestras:** no inventar datos; escalas fijas (`Bajo`, `Medio`, `Alto` para riesgos y aprovechamiento; `Correcto`, `Atención`, `Crítico` para el estado, provisionales); el contexto de cada sector está aislado; validar cifras antes de generar; jerarquía de fuentes agronómicas (FAO y OIV, MAPA y SIAR, organismos autonómicos, universidades; nada de blogs ni memoria del modelo), que sirve de base para el RAG opcional.
- **Recomendación solo cualitativa:** «no se generan dosis, duraciones o frecuencias exactas de riego sin método aprobado y datos suficientes». Quedan por definir por parte de Agrodevices el método de `aprov_pct` (hoy se muestra «—»), el criterio de las bandas y umbrales, el de `umbral_manejo`, el de los riesgos y el del estado. Ellos mismos se preguntan si la recomendación debe incluir dosis.

**Qué aporta nuestro proyecto y cómo encaja**
- **Valor añadido del TFM:** una dosis en mm calculada por el motor (FAO-56) con una predicción de humedad. Encaja con su regla, que permite cuantificar con un método aprobado y datos suficientes de suelo, cultivo y clima: el motor solo da dosis cuando hay textura, cultivo y tipo de riego, y si no, el informe es cualitativo como el suyo. Se presenta como **indicativa**.
- **Los datos manuales por sector coinciden con los parámetros mínimos por parcela** (cultivo y tipo de riego), pero ellos **no recogen la textura del suelo**, que el motor necesita.
- **Profundidad radicular activa:** ellos la estiman con las gráficas; el motor usa la de tabla por cultivo. Se pueden mostrar ambas.
- **Decisión pendiente de Laia: formato de salida.** El informe de Agrodevices es HTML con su plantilla y PDF por Chromium; lo decidido el 22 de octubre de 2026 para el TFM es PDF con ReportLab. Opciones: (a) mantener ReportLab con la misma estructura de páginas; (b) adoptar su plantilla HTML y generar el PDF con Chromium (Playwright, gratuito). Hay que decidir si se quiere partir de su plantilla o replicar su estructura.

### Datos de sensores
- Los sensores son de Agrodevices. Llegan **tramas hexadecimales** al servicio de la empresa, donde se **normalizan**. Las tramas originales **se guardan en la empresa**.
- Si la normalización detecta un valor erróneo (fuera de rango, etc.), lo **interpola** con lecturas cercanas (anteriores y posteriores).
- Cada lectura lleva una marca: **`SMMi` si está interpolada, `SMM` si es real** (estructura exacta por confirmar con una muestra).
- En esta aplicación se guarda el valor ya normalizado más una marca de interpolación (`is_interpolated`). **Los originales no se guardan aquí**: viven en la empresa. Esto cambia el principio de "conservar originales" para los sensores.
- Interpolar es algo habitual: en un mes puede haber unos 130 registros interpolados sin que sea un error. Referencia para el detector futuro. **Con el CSV real (3 de noviembre de 2026):** el 16,6 % de las filas son interpoladas (`SMMi`), unas 480 por unidad y mes, no 130; ver «Datos reales de Agrodevices recibidos».
- **Resolución:** los sensores miden cada 15 minutos (96 lecturas al día por sensor). Se guardan **sin resumir** en `sensor_reading`. Las decisiones son **diarias**: la recomendación diaria usa el primer día de la predicción y la semanal, los siete.
- **Hora de corte:** cada "día" es la ventana de 24 horas que termina a las 05:00, porque la recomendación se calcula con los datos guardados a esa hora; así el modelo no usa lecturas posteriores al momento de decidir (fuga de información del futuro). Las 05:00 son UTC (según el documento de entrega).
- **Gold:** las lecturas de cada ventana se resumen en último valor, media, mínimo y máximo; la meteorología ya es diaria. Un único modelo con salidas a 7 días sirve para la recomendación diaria y la semanal.
- **Datos necesarios (orientación, sin garantía):** lo que cuenta son los días distintos de información y las estaciones cubiertas, no las filas. Se medirá con una curva de aprendizaje.

### Previsiones meteorológicas
- **La ET0 la entrega Open-Meteo** ya calculada (FAO-56 Penman-Monteith, mm al día), en la previsión y en el histórico. No se calcula en el proyecto. Es una estimación de su modelo, no una medición. Comprobado con peticiones reales el 16 de octubre de 2026.
- **El servicio de ejecuciones anteriores solo ofrece valores por horas**: pedir la ET0 o la lluvia diaria da error. Se descargan por horas y se suman por días.
- **Historial de previsiones para entrenar: solo las ejecuciones anteriores de Open-Meteo** (historia desde enero de 2024), sin construir un segundo histórico con capturas propias (decisión de Laia, 16 de octubre de 2026; sustituye a la idea de combinar dos orígenes). La previsión de cada día se descarga igualmente para operar y queda reflejada en `recommendation.inputs_snapshot`. Pendiente: comprobar que los valores de las ejecuciones anteriores se parecen a los de la previsión real, porque el modelo se entrena con unos y se usa con los otros. Si los sensores tienen datos anteriores a enero de 2024, esa parte no tendrá previsiones reales.
- **Para qué se guarda el histórico:** para entrenar y evaluar. Una vez entrenado, para predecir solo hace falta una ventana reciente (los últimos 7 días de las entradas del modelo). Se conserva igualmente para reentrenar con más datos y estaciones, comprobar si el modelo sigue acertando y repetir los experimentos. Política de conservación de las lecturas de 15 minutos: pendiente, según el espacio disponible.
- Usar el histórico (reanálisis) como si fuera la previsión queda descartado para entrenar: el modelo vería previsiones perfectas y parecería mejor de lo que es.

### Imágenes de satélite (Sentinel-2)
- **Corrección:** el satélite de la plataforma de la empresa no es de la NASA, sino **Sentinel-2**, del programa **Copernicus de la Agencia Espacial Europea (ESA)**, según la demo de la plataforma Zahorí (16 de octubre de 2026).
- La plataforma descarga las imágenes de Copernicus, calcula el **NDVI** (índice de vegetación) en píxeles de 10 m, lo agrupa por sector de la parcela y lo combina con las sondas en un diagnóstico. En la demo el pase es cada 2,5 días y se descartan las imágenes con nubes.
- **Papel en este proyecto: ninguno por ahora.** La recomendación de riego no lo necesita (balance de agua con la humedad del suelo, la lluvia, la ET0 y los parámetros de la parcela). Decisión de Laia el 16 de octubre de 2026: se **elimina por completo del documento de entrega**. Si algún día se usara, haría falta una tabla nueva (una fila por sector y fecha de pase), que no está en el esquema de 10 tablas, y columnas opcionales en el Gold. Posible experimento si sobra tiempo: comparar el modelo con y sin NDVI.
- **Licencia:** datos Copernicus Sentinel de acceso libre y gratuito, citando la fuente ("Copernicus Sentinel data" y el año).
- Según la demo (pendiente de confirmar): las sondas son de tipo FDR, miden a 10, 20, 30 y 50 cm y envían datos cada 12 horas; la plataforma gestiona riegos programados.

### Alertas del MVP
- Las alertas para el cliente son **agronómicas**: lluvia fuerte prevista y suelo muy seco, y avisos del mismo estilo que interesen al agrónomo.
- **Los umbrales ("muy seco", "lluvia fuerte") están pendientes**: dependen de parámetros del suelo y de criterio agronómico. No se inventan.
- **No hay aviso de interpolación** en el MVP: distinguir lo normal de un error requiere un umbral, y eso es el detector de anomalías, que queda fuera del MVP.
- Se mantiene el historial de alertas de las pantallas acordadas, con alertas nuevas desde la última consulta; la tabla `alert` y su marcador no cambian.

### Estructura
- Un repositorio y un único paquete Python con toda la lógica (monolito modular).
- Dos procesos que comparten el paquete y la base de datos: **web** (interfaz) y **tareas** programadas. Las tareas diarias descargan y guardan la meteorología y los datos de sensores, y generan las alertas agronómicas. El motor de riego y los informes se ejecutan **a petición del usuario desde la web**, para una parcela, con los datos ya guardados.
- Los procesos solo se comunican a través de la BBDD. Abrir la web no descarga meteorología, no entrena ni recalcula.
- La interfaz no calcula: al pulsar "solicitar informe de hoy" llama a una función del paquete (por ejemplo `generate_report(plot_id)`) que ejecuta el motor, guarda la recomendación y el informe y devuelve el resultado; la página solo lee y muestra el informe. Esa función se ejecuta dentro del proceso web, por lo que el usuario de BBDD de la web necesita permiso de escritura.
- Los cálculos usan los datos guardados a las 05:00; no se descarga nada al pedir un informe. Cada informe indica la fecha de los datos que usó.
- Una recomendación por parcela y día: si se pide de nuevo, se actualiza. Cada informe muestra siempre la fecha de su recomendación; si hoy no se ha pedido ninguno, se muestra el último con su fecha, junto al botón de solicitar el de hoy.
- El LLM no se ejecuta en la web: la web solo muestra el informe ya generado. Como el modelo es local y la web puede estar en la nube, la explicación del LLM la genera un proceso aparte en el ordenador de Laia, sobre recomendaciones ya calculadas, y la web muestra la de la plantilla hasta que esté disponible. Para la defensa se puede ejecutar todo en local.
- Entrenar modelos se hace aparte, fuera de las tareas diarias; la aplicación usa versiones identificadas.

### Interfaz
- Streamlit para el MVP.

### Idioma
- Identificadores en inglés: variables, funciones, módulos, tablas y columnas, incluida la carpeta del motor de riego (nombre en inglés pendiente de acordar, por ejemplo `irrigation_engine/`).
- En español: comentarios, docstrings, interfaz, informes, documentación y mensajes de error.
- Se mantiene un glosario inglés–español en `docs/glosario.md`.
- Excepción: las columnas meteorológicas de `forecast_data` e `historical_weather` mantienen los nombres de la tabla de sensores de Agrodevices (`tMax`, `rhMedia`, `eto`...), porque la empresa será quien use la app. Excepto `weather_code`.
- Al terminar, el proyecto se traducirá para el portfolio; solo habrá que traducir textos.

### Usuarios y parcelas (MVP)
- Cuentas y asignaciones de parcelas **precargadas**, creadas por nosotros.
- Sin registro público, sin panel de administración, sin alta ni baja de parcelas por parte del usuario.
- Una cuenta equivale a un usuario (una persona). No hay cuentas de empresa con varios usuarios en el MVP.
- Las parcelas y sus sensores ya existen en la BBDD y se asignan a una cuenta.
- Las coordenadas son un atributo de la parcela, **no** su identificador.
- Los permisos se comprueban en el servidor (datos, informes e historial).
- Para la presentación se crearán un par de parcelas de prueba.

### Coste
- Todo gratuito durante el TFM. La empresa lo adaptará después a sus necesidades.

### Modelo de datos
Lista de entidades y diccionario en [base_de_datos.md](base_de_datos.md). Son 12 tablas (PostgreSQL con pgvector; antes eran 10 en MySQL). Decisiones de fondo:
- Cada parcela pertenece a un único usuario (`plot.user_id`); no hay tabla intermedia de asignación. Una parcela no se puede compartir entre usuarios hasta la mejora de organizaciones, que de todos modos cambiaría el esquema (`organization_id` en parcela y usuario).
- Parámetros de parcela dentro de `plot`, sin tabla de periodos de validez. La reproducibilidad queda cubierta por la copia de entradas en `recommendation`.
- Lo que se puede calcular no se guarda: la etapa y el Kc se calculan a partir del cultivo y de `cycle_start_date`. Excepción: `growth_stage_manual` y `kc_manual` opcionales, que prevalecen si se rellenan. La profundidad radicular se deduce y no se guarda por ahora.
- El marcador de alertas vistas es una columna de `plot` y no una tabla (`alert_view`).
- `alert`: una fila por alerta, modificada en su sitio; sin tabla de historial de eventos, sin gravedad y sin clave foránea al sensor en el MVP.
- `sensor` y `sensor_reading` separadas. Se descartó una única tabla de lecturas con sensor, parcela y profundidad repetidos en cada fila.
- **`sensor_reading` cambiada el 14 de octubre de 2026**: se añadió `is_interpolated` y se renombró `raw_value` a `value`, porque ya no es el valor original. Incluido ya en el esquema de PostgreSQL (el esquema de MySQL, donde se aplicó primero, se abandonó).
- Una sola tabla de previsiones (`forecast_data`) en lugar de captura más valores, que nunca se sobrescribe. El histórico (`historical_weather`) se sobrescribe cuando llegan revisiones, porque `recommendation` guarda una copia de lo que usó.
- `recommendation` guarda siempre esa copia, también si el resultado es "datos insuficientes". *(Actualizado el 22 de octubre de 2026: ya no es una por parcela y día, sino inmutable y versionada, con dos estados y plan de siete días; ver la sección de esa fecha.)* Sin porcentaje de confianza.
- `report` se conserva con el texto ya redactado y un historial de informes generados: queda preparada para los textos del LLM (columna `generator`) y permite compararlos con la plantilla.
- Sin tabla de riegos (`irrigation_event`) hasta disponer de datos de riego.
- `weather_code` guarda el código WMO y no el texto; la traducción se hace al mostrarlo.
- Las fechas y horas (`TIMESTAMPTZ`) se guardan en UTC.
- `sensor_reading` es única por (`sensor_id`, `measured_at`) para no duplicar lecturas al reimportar.

### Descartado
- **Agente interactivo o chat**: el informe es automático, nadie conversa con una IA ni le pasa datos ni instrucciones.
- **Que un LLM calcule la recomendación**: la calcula el motor determinista.
- **Usar un modelo de lenguaje solo con un prompt** como sustituto del entrenamiento. *(Revocado el 22 de octubre de 2026: se pasa a un LLM por API con prompt, ejemplos y RAG.)*
- **Modelo de ML para detectar anomalías**: sin fallos etiquetados no se puede entrenar ni evaluar bien.
- **Login con Google**: los clientes son empresas agrónomas sin por qué tener cuenta de Google.
- **Vincular parcelas por coordenadas** como identificador: riesgo de acceso a parcelas ajenas e imprecisión.

## 2. Provisional (pendiente de verificar con pruebas)

| Pieza | Propuesta | Estado |
|---|---|---|
| Tareas diarias | GitHub Actions (cron) | Sin implementar. A verificar: retrasos del cron; pausa tras 60 días sin actividad en repos públicos. Para la defensa se puede ejecutar todo en local |
| Base de datos | Aiven PostgreSQL con pgvector (plan gratuito) | **Esquema aplicado y validado** en Docker local y en Aiven (PostgreSQL 18.6). A verificar: umbral de inactividad antes de apagarse; si 1 GB basta para el histórico y las lecturas |
| Web | Streamlit Community Cloud | Sin implementar. A verificar: que funcione el login; el cifrado de los secretos no se detalla en la documentación |
| Login | Auth0 con `st.login` (OIDC, usuario y contraseña) | **Probado en local el 8 de octubre de 2026 con éxito.** Plan gratuito de Auth0: hasta 25.000 usuarios activos al mes, sin tarjeta. Pendiente: Streamlit Community Cloud (ver nota) |
| Login, plan B | `streamlit-authenticator` | Si permite usuarios desde BBDD en lugar de YAML |
| Entorno de ML | PyTorch con GPU NVIDIA, en un entorno aparte, **solo para el modelo de lenguaje**; el modelo de predicción es clásico y no necesita GPU | No he podido confirmar que PyTorch publique versiones con GPU para Python 3.14 en Windows; Laia también tiene Python 3.12 instalado. Verificar en pytorch.org antes de instalar |
| Modelo de lenguaje | *(Sustituido el 22 de octubre de 2026: LLM por API con prompt, ejemplos y RAG; proveedor y coste por decidir.)* Antes: modelo abierto de 3 a 8 mil millones de parámetros con QLoRA (Unsloth o PEFT) | Según guías secundarias, en 8 GB caben 3–4B con holgura y 7–8B muy justos. Confirmar con la documentación oficial y con una prueba. Modelo concreto, licencia y calidad en español, por decidir |

Notas:
- Login en Community Cloud (según hilos del foro de Streamlit, relatos de usuarios y no documentación oficial): para una app desplegada el `redirect_uri` tiene la forma `https://<app>.streamlit.app/~/+/oauth2callback` y debe coincidir exactamente con *Allowed Callback URLs* de Auth0. Desde finales de abril de 2026 hubo fallos con Auth0 (`mismatching_state: CSRF Warning`) y el remedio fue fijar `streamlit[auth]==1.56.0` y `Authlib==1.6.11`; su estado actual se desconoce. Plan B: alojar la web en otro sitio o usar `streamlit-authenticator`.
- La prueba de login vive en `data/login_test/` (ignorada por git) y se borra al terminar. Streamlit lee `.streamlit/secrets.toml` de la carpeta desde la que se lanza, y los valores del `.toml` llevan comillas. Procedimiento en [puesta_en_marcha.md](puesta_en_marcha.md).
- Apps de Community Cloud: se duermen tras 12 h sin tráfico y cualquier visitante puede despertarlas.
- Render queda como plan B solo para la web: se duerme a los ~15 min y su base de datos gratuita caduca; no ofrece Cron Jobs gratis.
- Docker: el PostgreSQL local de pruebas es el contenedor `irrigation-pg` (imagen `pgvector/pgvector:pg17`, puerto 5433, configuración en `.env.docker-pg`, ignorado por git). El contenedor `irrigation-mysql`, su volumen y `.env.docker` (esquema MySQL abandonado) se borraron el 22 de octubre de 2026 a petición de Laia. La base MySQL `irrigation_app` de Aiven la borra Laia desde la consola (el servicio MySQL tiene también `defaultdb` del proyecto antiguo, que no se toca).

## 3. Mejoras futuras (fuera del MVP)

1. Límite de parcelas por cuenta o modelo de suscripción de pago.
2. Alta de parcela por **código de activación** (vincula una parcela ya existente) y archivado de parcelas (borrado lógico, conservando el histórico).
3. Registro de usuarios, con aprobación previa o por invitación.
4. Cuenta por empresa, con varios usuarios que compartan parcelas y tengan permisos diferentes (roles). Hará falta una entidad de organización, con `organization_id` en usuario y en parcela, y un rol de usuario; la empresa dejaría de ser un texto libre en `user.company_name` y pasaría a ser una tabla. Supone modificar el esquema del MVP; entre otras cosas, el marcador de alertas vistas tendría que pasar a ser por usuario y parcela (tabla propia con `user_id` y `plot_id`).
5. Tabla `alert_history` con el historial de cambios de cada alerta (evolución intermedia, p. ej. cambios de gravedad).
6. Historial de parámetros de parcela con periodos de validez.
7. Entidad `irrigation_event` (riegos aplicados), cuando haya datos de riego.
8. Caducidad de `growth_stage_manual` y `kc_manual`: registrar la fecha de la corrección y avisar al usuario de que revise o actualice el valor pasado cierto tiempo. Posible columna opcional de profundidad radicular manual (`root_depth_cm_manual`).
9. Guardar las revisiones del histórico meteorológico (varias filas por parcela y día, insertando una nueva solo si los valores cambian).
10. Historial de recomendaciones por parcela accesible para el usuario (los datos ya se guardan; falta la pantalla).
11. **Detector de anomalías por reglas**: proporción de lecturas interpoladas por sensor, días seguidos con interpolación y sensor mudo, calibrado con la tasa habitual (unos 130 interpolados al mes). Generaría alertas de calidad de datos.
12. **Alertas de calidad de datos solo para el personal de la empresa**, cuando existan roles.
13. Modelo de detección de anomalías en lecturas reales, evaluado contra las reglas y con fallos sintéticos, solo si hay datos suficientes y la comparación lo justifica.
14. Corrección del sesgo meteorológico, si no entra antes como opcional (A).
15. *(Adelantado al MVP el 22 de octubre de 2026, con pocos documentos al principio.)* **Conocimiento agronómico con RAG** (búsqueda de fragmentos de documentación técnica, por ejemplo la guía FAO-56, para que el modelo de lenguaje los use al explicar). Decisión de Laia el 17 de octubre de 2026: queda como mejora futura. Motivos: hace falta reunir los documentos y comprobar su licencia, añade un índice de búsqueda, y un modelo pequeño ajustado puede mezclar el texto recuperado con las cifras del motor, además de ser más difícil de evaluar. Mientras tanto, alternativa barata: asociar a cada regla del motor un texto explicativo fijo de una fuente fiable.

## 4. Pendientes

**De la empresa (Agrodevices):**
- Qué miden los sensores (el nombre `SMM` sugiere humedad del suelo, sin confirmar) y en qué unidad, calibración, y cómo identifican cada sensor y su parcela. Zona horaria de las lecturas.
- Estructura exacta de las marcas `SMM` y `SMMi`: ¿una columna con ese texto o dos columnas?
- Los 25.000 registros: número de sensores y parcelas, frecuencia de medida, rango de fechas, y si hay lecturas de las distintas estaciones del año.
- Diccionario de sensores, histórico adicional, registros de riego (la demo muestra riegos programados: confirmar si hay un registro que se pueda usar) y parámetros agronómicos.
- Imágenes de satélite (fuera del proyecto por ahora): solo si algún día se usan, confirmar el acceso a los NDVI de la plataforma, su formato y su histórico.
- Frecuencia de ingesta de sensores: una vez al día, a las 05:00, junto con la meteorología (según Laia, en principio). El motor debe tener en cuenta la antigüedad del estado del suelo.

**De diseño:**
- **Estructura de salida del motor** (`reasons`, `limitations`...): es previa a generar los datos de entrenamiento del LLM, porque los ejemplos se derivan de ella.
- Umbrales de las alertas agronómicas: "suelo muy seco" y "lluvia fuerte".
- Reglas agronómicas del motor y tratamiento de datos insuficientes, con apoyo agronómico.
- Dónde viven los valores de referencia por cultivo: archivo de configuración o tabla.
- Nombre en inglés de la carpeta del motor de riego.
- Informe en PDF (requisito del MVP; librería: ReportLab): diseñar la plantilla (con la estructura de diez secciones del Word) y decidir qué gráficas lleva (por ejemplo, humedad del suelo pasada y prevista por profundidad, lluvia y ET0 previstas, y la dosis recomendada). Es una capa de presentación que se hace cuando el informe ya funcione.
- Elegir una parcela, cultivo y sector como caso inicial.
- Cómo se calcula la perspectiva futura del informe semanal (probablemente con el modelo B).
- Primera consulta de alertas: cuántas se muestran; y momento exacto de actualización del marcador.
- Qué se recorta de la aplicación si el plazo aprieta (el login en la nube, el historial de alertas y el despliegue son candidatos).
- Plan semanal de ocho semanas, cuando todo esté claro.

**Del modelo de ML (a resolver con los datos reales):**
- ~~Fechas, sensores y parcelas de los 194.000 registros~~ **Resuelto el 3 de noviembre de 2026 con el CSV real** (junio de 2024 a noviembre de 2026, 190 unidades, 35 con más de un año; ver «Datos reales de Agrodevices recibidos»). Pendiente: qué unidades se usan como parcela de referencia.
- Regla para marcar posibles riegos (umbral de subida y ventana, con la precipitación de Open-Meteo): ajustarla con los datos reales. Riesgo: en parcelas que se riegan a menudo quedarán pocos ejemplos limpios para los horizontes largos; si es así, esos horizontes se marcan `provisional` y se apoyan en datos públicos de secano.
- Fuente de la meteorología de los días recientes: el reanálisis llega con ~5 días de retraso, así que en producción habrá que usar la previsión; entrenar con la misma fuente que habrá al predecir.
- Si ayuda predecir el cambio de humedad en lugar del nivel (se prueba en el backtesting).
- Previous Runs y la API de ejecuciones individuales (Single Runs): comprobar cobertura y si incluyen la ET₀. Single Runs sin verificar.
- Columnas exactas de `ml_model` y `ml_prediction` (decididas: se crean; ver la sección del modelo de ML), con los archivos de los modelos fuera de la base de datos. Pendiente escribirlas en `sql/create_schema.sql` cuando el contrato entre el ML y el motor esté cerrado.
- Dónde corre la tarea diaria: hoy no corre en ningún sitio, porque solo existen conectores de Open-Meteo del servicio antiguo, por adaptar. Si acaba en GitHub Actions, el archivo del modelo tendrá que estar disponible allí (los de LightGBM suelen ser pequeños). Para la defensa se puede ejecutar en local.
- Unidad de la humedad del sensor y cómo se pasa a lo que necesita el motor (varias profundidades). **Actualización del 3 de noviembre de 2026:** la unidad es la frecuencia en bruto y la profundidad de cada canal no viene en el CSV; ver «Datos reales de Agrodevices recibidos» para la lista de lo que falta.

**A verificar al implementar:**
- Unidades por defecto de las columnas meteorológicas en Open-Meteo.
- API de ejecuciones anteriores: ya verificado que solo ofrece valores por horas. Pendiente: la diferencia entre fecha de descarga y de emisión, si la suma por días coincide con el valor diario de la previsión, y qué modelo concreto sirve el reanálisis.
- Fecha del primer registro de los sensores, para saber qué parte del histórico tiene previsiones reales asociadas.
- Compatibilidad de PyTorch con GPU y la versión de Python (3.14 frente a 3.12), y qué GPU exacta es la de 8 GB (se sabe que es NVIDIA GeForce).
- Modelo de lenguaje concreto, su licencia y requisitos reales de VRAM.
- Estimar si el almacenamiento gratuito aguanta el histórico y las lecturas: a 15 minutos son unas 35.000 lecturas al año por sensor, y con muchos sensores el gigabyte gratuito de Aiven puede quedarse corto.
- Revisar las condiciones comerciales de Open-Meteo para separar el TFM educativo del despliegue empresarial.

**Repositorio antiguo de referencia** (solo lectura, `agrodevices-meteo-app`): solo se han revisado `src/providers/open_meteo.py` y `sql/create_schema.sql`. El resto del servicio se conoce por su descripción.

## 5. Estado actual

**Hecho:**
- Estructura del repositorio y documentación.
- Esquema de la base de datos (12 tablas, PostgreSQL 17 con pgvector 0.8.7): relaciones, diccionario, ERD y SQL. Validado en Docker local y en Aiven (PostgreSQL 18.6), con las 12 tablas, 10 claves foráneas, 13 `CHECK`, 8 unicidades y 36 pruebas de restricciones correctas (incluidas inmutabilidad de `recommendation`, cadena de versiones y búsqueda vectorial). Sustituye al esquema de MySQL de 10 tablas, que estaba vacío. Las tablas del servicio antiguo (`defaultdb`) no se han tocado.
- Script de aplicación del esquema, `scripts/apply_schema.py`, con salvaguardas.
- Login con Auth0 y `st.login` probado en local.
- Entorno de Python de la aplicación con las dependencias fijadas.
- Alcance del TFM reorientado a ML/DL/IA generativa (14 de octubre de 2026).

**Siguiente:**
1. Cerrar el alcance: concretar el diseño de los modelos y recortar la aplicación.
2. Cargar y analizar los 25.000 registros y definir la estructura de salida del motor.
3. Borrar la base MySQL `irrigation_app` del servicio MySQL de Aiven (Laia, desde la consola).
4. Conector de Open-Meteo y las dos parcelas de prueba.
5. Plan de ocho semanas.
