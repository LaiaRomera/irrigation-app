# Glosario inglés-español

Los identificadores del código van en inglés; esta tabla da su equivalente en español. Se amplía a medida que aparecen términos nuevos.

| Inglés (código) | Español | Notas |
|---|---|---|
| plot | parcela | Unidad de terreno para la que se calcula la recomendación |
| sensor | sensor | |
| forecast | previsión | Captura de una previsión meteorológica |
| historical | histórico | Datos pasados; reanálisis, no medición local |
| irrigation engine | motor de riego | Cálculo determinista de la recomendación |
| recommendation | recomendación | |
| alert | alerta | |
| report | informe | Diario o semanal |
| user | usuario | La tabla se llama `app_user`, porque `user` es palabra reservada en PostgreSQL |
| tMax / tMin / tMedia | temperatura máxima / mínima / media | °C. Nombres de la tabla de sensores de Agrodevices |
| rhMax / rhMin / rhMedia | humedad relativa máxima / mínima / media | % |
| vientoMax / vientoMin / vientoMedia | viento máximo / mínimo / medio a 10 m | km/h |
| sensTermicaMax / sensTermicaMin | sensación térmica máxima / mínima | °C |
| eto | evapotranspiración de referencia (ET0) | mm/día, FAO-56 |
| rainMm | precipitación | mm |
| probPrecipitacion | probabilidad de precipitación | %, solo previsiones |
| uvmax | índice UV máximo | Solo previsiones |
| weather_code | código meteorológico | Código WMO; la traducción al español se hace al mostrarlo |
| captured_at | fecha de captura de la previsión | |
| lead_days | antelación de la previsión | Días entre la emisión y el día previsto |
| growth_stage_manual / kc_manual | etapa / Kc fijados a mano | Vacíos = los calcula el motor |
| Kc | coeficiente de cultivo | Se multiplica por la ET0 para estimar la demanda de agua del cultivo (ETc = ET0 × Kc) |
| cycle_start_date | inicio del ciclo del cultivo | Siembra u otro hito (brotación en leñosos). De ella se deduce la etapa |
| reanálisis | reanálisis meteorológico | Reconstrucción del tiempo pasado con modelos y observaciones. No es una medición local |
| inputs_snapshot | copia de las entradas usadas | Lo que usó una recomendación, guardado para poder reproducirla |
| net_dose_mm / gross_dose_mm | dosis neta / dosis bruta | La bruta es la neta dividida por la eficiencia del riego |
| sensor_reading | lectura de sensor | Valor normalizado por la empresa (puede estar interpolado), con su marca de interpolación y su indicador de calidad |
| quality_flag | indicador de calidad | Marca una lectura como correcta o sospechosa sin borrarla |
| job_run | ejecución de una tarea | Registro de cada ejecución del proceso de tareas |
| generator | generador del informe | Plantilla en el MVP; modelo de lenguaje en el futuro |
| is_active | activo | Desactivar sin borrar, para no perder el histórico |
| is_interpolated | interpolado | Sí si la empresa sustituyó un valor erróneo por una interpolación de lecturas cercanas |
| SMM / SMMi | marca de lectura real / interpolada | Marca de los datos de Agrodevices; estructura exacta por confirmar |
| value | valor normalizado | Valor tras la normalización de la empresa; no es el original, que es la trama hexadecimal |
| baseline | línea base | Método sencillo con el que se compara un modelo para saber si de verdad aporta |
| deep learning | aprendizaje profundo | Redes neuronales; aquí, solo en el modelo de lenguaje |
| machine learning clásico | aprendizaje automático clásico | Modelos como regresiones o árboles de decisión (*gradient boosting*); aquí, para predecir la humedad del suelo |
| LLM | modelo de lenguaje grande | Modelo que genera texto; aquí redacta las explicaciones de los informes |
| fine-tuning | ajuste fino | Entrenar un poco más un modelo ya existente con ejemplos propios |
| QLoRA | ajuste fino con adaptadores y cuantización | Permite ajustar un modelo en una GPU pequeña entrenando solo unos pocos parámetros |
| token | token | Unidad de texto que lee o escribe un modelo de lenguaje; más tokens, más cómputo |
