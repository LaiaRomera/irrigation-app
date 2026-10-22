# Base de datos

Referencia del modelo de datos. Complementa:
- [erd.md](erd.md): el diagrama entidad-relación.
- [`sql/create_schema.sql`](../sql/create_schema.sql): el esquema ejecutable, que es la fuente de verdad de tipos y restricciones.
- [decisiones.md](decisiones.md): por qué se decidió cada cosa.

Si cambia el esquema, hay que actualizar estos cuatro sitios.

## 1. Resumen

- **Motor:** PostgreSQL 17 con la extensión **pgvector** (búsqueda por similitud de vectores, para el RAG). Base de datos `irrigation_app`, codificación UTF-8. Sustituye al esquema de MySQL, que se abandonó el 22 de octubre de 2026 con la base todavía vacía (ver [decisiones.md](decisiones.md)).
- **12 tablas, 10 claves foráneas, 13 restricciones `CHECK` y 8 unicidades**, validadas en PostgreSQL 17 con pgvector 0.8.7 (Docker local) y en Aiven PostgreSQL 18.6 con pgvector 0.8.7.
- **Identificadores en inglés.** Excepción acordada: las columnas meteorológicas conservan los nombres de la tabla de sensores de Agrodevices (`tMax`, `rhMedia`, `eto`...), porque la empresa será quien use la app. PostgreSQL pasa a minúsculas los nombres sin comillas, así que en la base son `tmax`, `rhmedia`..., el mismo nombre sin distinguir mayúsculas. Excepto `weather_code`.
- **La tabla de usuarios se llama `app_user`**, porque `user` es una palabra reservada en PostgreSQL.
- **No se borra:** usuarios, parcelas y sensores se desactivan con `is_active`. Las claves foráneas impiden el borrado de una fila a la que otras hacen referencia.
- **Recomendaciones inmutables:** un disparador (trigger) impide modificar o borrar una fila de `recommendation`; cada recálculo crea una versión nueva.
- **Fechas y horas (`TIMESTAMPTZ`):** se guardan en UTC. La zona horaria de las lecturas de sensores (`measured_at`) depende de cómo las entregue Agrodevices y está pendiente de confirmar. Los días (`fecha`) son en la zona horaria de la parcela.
- **Tipos orientativos:** los exactos están en el SQL.

**Estado de cada columna:** **Seguro** (acordado), **Pendiente** (columna ya creada, sin datos hasta que Agrodevices o la revisión agronómica los aporten; puede estar vacía) o **A decidir**.

## 2. Entidades

| Grupo | Tabla | Qué guarda |
|---|---|---|
| Acceso | `app_user` | La cuenta. Una cuenta = una persona |
| Parcela | `plot` | La parcela, sus parámetros agronómicos y el marcador de alertas vistas |
| Sensores | `sensor` | Un sensor instalado en una parcela |
| Sensores | `sensor_reading` | Cada lectura de un sensor, con su indicador de calidad |
| Meteorología | `forecast_data` | Previsiones por captura; nunca se sobrescriben |
| Meteorología | `historical_weather` | Reanálisis observado; una fila por parcela y día |
| Alertas | `alert` | Una fila por alerta, con su estado actual |
| Motor e informes | `recommendation` | Resultado del motor, inmutable y versionado, con copia de lo que usó |
| Motor e informes | `report` | Informe generado a partir de una recomendación |
| Operación | `job_run` | Registro de las ejecuciones de las tareas |
| Conocimiento (RAG) | `knowledge_document` | Documento agronómico con su procedencia y licencia |
| Conocimiento (RAG) | `knowledge_chunk` | Fragmento del documento, con su vector de significado |

## 3. Decisiones de modelado

- **Cada parcela pertenece a un único usuario** (`plot.user_id`); no hay tabla de asignación. Una parcela no se puede compartir entre usuarios hasta la mejora de organizaciones, que de todos modos cambiaría el esquema.
- **Los parámetros agronómicos están dentro de `plot`** y se actualizan en su sitio, sin tabla de periodos de validez. La reproducibilidad la cubre la copia de entradas en `recommendation`.
- **Lo que se puede calcular no se guarda.** La etapa del cultivo y el Kc se deducen del cultivo y de `cycle_start_date`. Excepción acordada: `growth_stage_manual` y `kc_manual`, opcionales; vacíos significa que el motor calcula, rellenos significa que prevalecen. La profundidad radicular tampoco se guarda por ahora.
- **El marcador de alertas vistas es una columna de `plot`** (`alerts_last_seen_at`), no una tabla: al tener cada parcela un único usuario, el marcador por usuario y parcela equivale a uno por parcela. Las alertas nuevas son las de la parcela con `updated_at` posterior a ese marcador; las activas se muestran siempre.
- **`alert` es una fila por alerta, modificada en su sitio** (estado y fechas), sin tabla de historial de cambios en el MVP. Un disparador mantiene `updated_at` al día. Si el fallo es de un sensor, la descripción lo nombra; no hay clave foránea al sensor. Sin campo de gravedad por ahora.
- **`sensor` y `sensor_reading` son tablas separadas:** la parcela y la profundidad se guardan una sola vez, y la clave foránea impide lecturas de sensores no registrados.
- **`sensor_reading` guarda el valor normalizado por la empresa, no el original.** Los sensores envían tramas hexadecimales a la empresa, que las normaliza e interpola los valores erróneos con lecturas cercanas; las tramas originales se quedan allí. Aquí se guarda el valor y la marca `is_interpolated`, que sirve al motor y a un futuro detector de anomalías. Interpolar es habitual (unos 130 registros al mes pueden ser normales).
- **`forecast_data` es una sola tabla** (no captura más valores). Nunca se sobrescribe con el histórico.
- **`historical_weather` se sobrescribe** cuando Open-Meteo revisa los valores de los últimos días. Es reanálisis, no medición local, y no contiene UV ni probabilidad de precipitación.
- **`recommendation` es inmutable y versionada.** Un recálculo no modifica la fila: crea una nueva con `version` + 1 y `supersedes_id` apuntando a la anterior. Guarda siempre una copia de lo que usó, también si el resultado es "datos insuficientes", por lo que el histórico meteorológico puede sobrescribirse sin perder reproducibilidad.
- **Dos estados separados en `recommendation`:** `decision` (qué se recomienda) y `validation_status` (cuánta confianza dan los datos y el método). No se mezclan en un solo campo.
- **Un cálculo contiene el plan de siete días** (`daily_plan`); el informe diario usa el primer día.
- **`report` cuelga siempre de una recomendación** y guarda el texto ya redactado. Admite varios generadores y versiones para la misma recomendación (plantilla de respaldo y modelo de lenguaje). El PDF no se guarda: se genera al pedirlo.
- **No hay tabla de riegos** (`irrigation_event`): los riegos quedan fuera del alcance.
- **Campos JSON (`JSONB`)** en `recommendation`, `report` y `job_run`: su estructura es provisional hasta diseñar el motor y el modelo de machine learning.
- **Base de conocimiento para el RAG** en dos tablas: `knowledge_document` conserva la procedencia, la licencia y a qué cultivo y región aplica cada documento; `knowledge_chunk` guarda cada fragmento con su vector.

## 4. Diccionario de datos

### `app_user`

| Columna | Tipo | Obligatoria | Estado | Descripción |
|---|---|---|---|---|
| `user_id` | entero grande, PK, generado | Sí | Seguro | Identificador interno |
| `email` | texto, único | Sí | Seguro | Se guarda en minúsculas (lo impone una restricción). Es lo que se cruza con el login |
| `name` | texto | Sí | Seguro | Nombre para mostrar en la interfaz |
| `company_name` | texto | No | Seguro | Texto libre. Con la mejora de organizaciones pasará a ser una tabla |
| `phone` | texto | No | Seguro | Texto, para conservar prefijos y espacios |
| `is_active` | sí/no, por defecto sí | Sí | Seguro | Desactivar la cuenta sin borrarla |
| `created_at` | fecha y hora | Sí | Seguro | |
| contraseña o identificador del proveedor de login | | | Pendiente | Con Auth0 no se guardaría contraseña, solo el email. Se decide al implementar el login definitivo |

### `plot`

| Columna | Tipo | Obligatoria | Estado | Descripción |
|---|---|---|---|---|
| `plot_id` | entero grande, PK, generado | Sí | Seguro | |
| `user_id` | entero grande, FK → `app_user` | Sí | Seguro | Dueño de la parcela |
| `name` | texto | Sí | Seguro | Nombre que le da el usuario |
| `latitude`, `longitude` | decimal | Sí | Seguro | Para pedir la meteorología. No son el identificador de la parcela |
| `altitude_m` | decimal | No | Seguro | De Open-Meteo; vacío si se desconoce |
| `timezone` | texto | Sí | Seguro | Zona horaria IANA (`Europe/Madrid`, `Atlantic/Canary`...). Puede haber parcelas en Canarias. Define qué es "hoy" en cada parcela |
| `alerts_last_seen_at` | fecha y hora | No | Seguro | Última consulta de alertas, compartida entre informe diario y semanal. Vacía si nunca se han consultado |
| `is_active` | sí/no, por defecto sí | Sí | Seguro | |
| `created_at` | fecha y hora | Sí | Seguro | |
| `crop` | texto | No | Pendiente | Cultivo |
| `cycle_start_date` | fecha | No | Pendiente | Siembra u otro hito que inicie el ciclo (por ejemplo, brotación en leñosos). De ella se deduce la etapa |
| `growth_stage_manual` | texto | No | Seguro | Etapa fijada a mano. Vacía = el motor la calcula a partir de `crop` y `cycle_start_date`. Si se rellena, prevalece |
| `kc_manual` | decimal | No | Seguro | Kc fijado a mano. Vacío = el motor lo deduce del cultivo y la etapa. Si se rellena, prevalece |
| `area_m2` | decimal | No | Pendiente | Superficie en m² (unidad de la fórmula del volumen); se muestra en hectáreas |
| `irrigation_sector` | texto | No | Pendiente | Sector de riego |
| `soil_texture` | texto | No | Pendiente | Arenoso, franco, arcilloso... |
| `field_capacity` | decimal | No | Pendiente | Capacidad de campo. Unidad y origen (valor típico o medido) por decidir |
| `wilting_point` | decimal | No | Pendiente | Punto de marchitez. Igual que la anterior |
| `allowed_depletion` | decimal | No | Pendiente | Agotamiento admisible antes de regar |
| `irrigation_efficiency` | decimal (0–1) | No | Pendiente | Eficiencia de la instalación. Restricción: mayor que 0 y como máximo 1 |
| `flow_rate_m3_h` | decimal | No | Pendiente | Caudal representativo de la instalación |

**Riesgo conocido:** una corrección manual (`growth_stage_manual`, `kc_manual`) se queda fijada hasta que alguien la borre, aunque la etapa real avance. Mitigación en el MVP: indicar en el informe cuándo se usa un valor manual. Mejora futura: fecha o caducidad de la corrección.

### `sensor`

| Columna | Tipo | Obligatoria | Estado | Descripción |
|---|---|---|---|---|
| `sensor_id` | entero grande, PK, generado | Sí | Seguro | |
| `plot_id` | entero grande, FK → `plot` | Sí | Seguro | Parcela donde está |
| `depth_cm` | entero | Sí | Seguro | Profundidad en centímetros, mayor que 0. Es lo único que varía entre sensores |
| `is_active` | sí/no, por defecto sí | Sí | Seguro | |
| `external_code` | texto, único | No | Pendiente | Código del sensor en Agrodevices, si existe. Puede haber varios vacíos |

Todos los sensores miden lo mismo y en la misma unidad (según Laia). **Qué variable y qué unidad: pendiente de confirmar con Agrodevices.** Importa: si se mide tensión del suelo en lugar de humedad volumétrica, el motor lo trata de otra forma.

### `sensor_reading`

| Columna | Tipo | Obligatoria | Estado | Descripción |
|---|---|---|---|---|
| `reading_id` | entero grande, PK, generado | Sí | Seguro | |
| `sensor_id` | entero grande, FK → `sensor` | Sí | Seguro | |
| `measured_at` | fecha y hora | Sí | Seguro | Zona horaria del dato por confirmar |
| `value` | decimal | Sí | Seguro | Valor normalizado por la empresa; puede estar interpolado. Unidad pendiente. No es el valor original: ese es la trama hexadecimal, que se guarda en la empresa |
| `is_interpolated` | sí/no, por defecto no | Sí | Seguro | Sí si la empresa lo interpoló con lecturas cercanas (marca `SMMi`); no si es una lectura real (`SMM`). Estructura exacta de la marca por confirmar con una muestra |
| `quality_flag` | texto | Sí | Seguro | Por ejemplo `ok` o `suspicious`. Los valores definitivos llegarán con el detector de anomalías, que queda fuera del MVP |
| `quality_reason` | texto | No | Seguro | Motivo si es sospechosa |
| `received_at` | fecha y hora | Sí | Seguro | Cuándo la recibimos (por defecto, el momento de insertar) |

Unicidad: (`sensor_id`, `measured_at`), para no duplicar una lectura si se vuelve a importar el mismo dato.

### Columnas meteorológicas comunes

Las unidades son las que Open-Meteo devuelve por defecto y que el conector antiguo no modifica. **Hay que verificarlas contra su documentación al implementar el conector.** Los nombres se muestran con las mayúsculas originales de la empresa; en la base son minúsculas.

| Columna | Tipo | Unidad | Solo previsión | Descripción |
|---|---|---|---|---|
| `tMax`, `tMin`, `tMedia` | decimal | °C | No | Temperatura máxima, mínima y media a 2 m |
| `rhMax`, `rhMin`, `rhMedia` | decimal | % | No | Humedad relativa máxima, mínima y media |
| `vientoMax`, `vientoMin`, `vientoMedia` | decimal | km/h | No | Viento a 10 m |
| `sensTermicaMax`, `sensTermicaMin` | decimal | °C | No | Sensación térmica |
| `eto` | decimal | mm/día | No | Evapotranspiración de referencia FAO-56, calculada por Open-Meteo |
| `rainMm` | decimal | mm | No | Precipitación total del día |
| `probPrecipitacion` | decimal | % | **Sí** | Probabilidad máxima de precipitación. No existe para días pasados |
| `uvmax` | decimal | | **Sí** | Índice UV máximo. No existe para días pasados; el UV de una previsión nunca se arrastra al histórico |
| `weather_code` | entero | | No | Código meteorológico WMO. Se guarda el código y no el texto; la traducción al español se hace al mostrarlo. Única columna meteorológica que no mantiene el nombre de la empresa (allí `estadoCielo` es texto) |

### `forecast_data`

Una fila por parcela, día previsto, antelación y origen. **Nunca se sobrescribe** con el histórico: conserva lo que se previó en cada captura, para evaluar después el error por antelación.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `forecast_id` | entero grande, PK, generado | Sí | |
| `plot_id` | entero grande, FK → `plot` | Sí | |
| `fecha` | fecha | Sí | Día previsto, en la zona horaria de la parcela |
| `captured_at` | fecha y hora | Sí | Cuándo descargamos la previsión. **No** equivale necesariamente a la hora de emisión del modelo |
| `lead_days` | entero | Sí | Antelación en días (0 o más). Se guarda y no se calcula porque, con la API de ejecuciones anteriores, `captured_at` sería la fecha de descarga y no la de emisión (por verificar en la documentación de Open-Meteo) |
| `source` | texto | Sí | Endpoint u origen de la previsión (previsión normal o ejecuciones anteriores) |
| columnas meteorológicas comunes | | | Incluidas `probPrecipitacion` y `uvmax` |

Unicidad: (`plot_id`, `fecha`, `lead_days`, `source`); repetir el refresco el mismo día actualiza la fila y no la duplica.

### `historical_weather`

Una fila por parcela y día observado. Es reanálisis de Open-Meteo, **no medición local**. Se sobrescribe cuando Open-Meteo revisa los valores de los últimos días.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `historical_id` | entero grande, PK, generado | Sí | |
| `plot_id` | entero grande, FK → `plot` | Sí | |
| `fecha` | fecha | Sí | Día observado, en la zona horaria de la parcela |
| `downloaded_at` | fecha y hora | Sí | Última descarga. Permite informar de la antigüedad de los datos usados |
| `source` | texto | Sí | Procedencia del dato (reanálisis de Open-Meteo). Modelo concreto por verificar |
| columnas meteorológicas comunes | | | Sin `probPrecipitacion` ni `uvmax` |

Unicidad: (`plot_id`, `fecha`).

### `alert`

| Columna | Tipo | Obligatoria | Estado | Descripción |
|---|---|---|---|---|
| `alert_id` | entero grande, PK, generado | Sí | Seguro | |
| `plot_id` | entero grande, FK → `plot` | Sí | Seguro | |
| `alert_type` | texto | Sí | Seguro | Evita duplicados: no se crea otra si ya hay una activa del mismo tipo en la parcela. Tipos concretos con el detector |
| `description` | texto | Sí | Seguro | Qué ha fallado; si es un sensor, lo nombra |
| `status` | texto | Sí | Seguro | `active` o `resolved`. Ver una alerta no la resuelve |
| `created_at` | fecha y hora | Sí | Seguro | |
| `updated_at` | fecha y hora | Sí | Seguro | Última modificación (la actualiza un disparador); las alertas nuevas son las posteriores a `plot.alerts_last_seen_at` |
| `resolved_at` | fecha y hora | No | Seguro | Vacía mientras esté activa |

### `recommendation`

**Inmutable y versionada.** Se calcula cuando el usuario lo solicita desde la web, con los datos guardados a las 05:00. Un solo cálculo contiene el plan de siete días; el informe diario usa el primer día. Si se recalcula, no se modifica la fila: se crea una versión nueva que apunta a la anterior. Un disparador rechaza cualquier `UPDATE` o `DELETE`. Los JSON tienen estructura **provisional** hasta diseñar el motor y el modelo de machine learning.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `recommendation_id` | entero grande, PK, generado | Sí | |
| `plot_id` | entero grande, FK → `plot` | Sí | |
| `period_start`, `period_end` | fecha | Sí | Primer y último día del plan (el día del cálculo y seis más). `period_end` ≥ `period_start` |
| `version` | entero | Sí | 1 en el primer cálculo; mayor o igual que 1 |
| `supersedes_id` | entero grande, FK → `recommendation` | No | Versión que sustituye. Vacío si y solo si `version` = 1 |
| `calculated_at` | fecha y hora | Sí | Instante del cálculo |
| `decision` | texto | Sí | Decisión para el primer día: `irrigate`, `postpone`, `do_not_irrigate` o `insufficient_data` |
| `validation_status` | texto | Sí | `validated` (datos críticos y método aplicables), `provisional` (hay estimaciones o incertidumbres relevantes) o `insufficient_data` (no se puede emitir dosis definitiva) |
| `daily_plan` | JSONB | No | Plan de siete días: lámina neta y bruta en mm, volumen solo si se conoce la superficie, y datos de cada día. Los valores desconocidos van a `null` |
| `reasons` | JSONB | No | Motivos estructurados (estado del suelo, demanda prevista, lluvia considerada, criterios aplicados) |
| `inputs_snapshot` | JSONB | Sí | Copia de lo que usó: parámetros de la parcela, etapa y Kc finales con su origen, datos meteorológicos, humedad prevista por el modelo de ML y su antigüedad. Cubre la reproducibilidad |
| `limitations` | JSONB | No | Supuestos, advertencias y limitaciones |
| `needs_review` | sí/no | Sí | Si requiere revisión humana. Por defecto, no |
| `method_version` | texto | Sí | Versión del método de cálculo |
| `parameter_set_version` | texto | Sí | Versión del conjunto de parámetros agronómicos |

Unicidad: (`plot_id`, `period_start`, `version`). Sin porcentaje de confianza: no hay una metodología que lo respalde.

### `report`

Informe generado a partir de una recomendación, con el texto ya redactado. El PDF no se guarda: se genera al pedirlo a partir de `content` y `text_es`. Las alertas **no** se guardan dentro: se consultan al abrir el informe, para que aparezcan las nuevas aunque se generara antes.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `report_id` | entero grande, PK, generado | Sí | |
| `recommendation_id` | entero grande, FK → `recommendation` | Sí | Recomendación en la que se basa. De ella salen la parcela y el periodo |
| `report_type` | texto | Sí | `daily` o `weekly` |
| `generated_at` | fecha y hora | Sí | |
| `content` | JSONB | Sí | Datos estructurados del informe, incluidas las fuentes citadas |
| `text_es` | texto | Sí | Texto en español ya redactado. No cambia si se modifica la plantilla |
| `generator` | texto | Sí | Plantilla (respaldo) o modelo de lenguaje |
| `generator_version` | texto | Sí | Versión de la plantilla o del modelo |
| `validation_checks` | JSONB | No | Resultado de la validación previa al PDF: cifras, unidades, secciones y referencias |

Unicidad: (`recommendation_id`, `report_type`, `generator`, `generator_version`).

### `job_run`

Una fila por ejecución del proceso de tareas. Sustituye al `api_log` del servicio antiguo, que registraba cada petición a Open-Meteo.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `job_run_id` | entero grande, PK, generado | Sí | |
| `job_name` | texto | Sí | Por ejemplo, el refresco diario |
| `triggered_by` | texto | Sí | `scheduled` o `manual` |
| `started_at` | fecha y hora | Sí | |
| `finished_at` | fecha y hora | No | Vacía mientras corre |
| `status` | texto | Sí | `running`, `success`, `partial` o `failed` |
| `plots_ok`, `plots_failed` | entero | No | Resultado por parcelas |
| `error_summary` | texto | No | **Nunca** claves ni contraseñas |
| `details` | JSONB | No | Resultado por paso, para la recuperación de trabajo pendiente |

Sirve para saber si ya hubo un refresco correcto hoy y para impedir dos ejecuciones simultáneas.

### `knowledge_document`

Documento agronómico de la base de conocimiento del RAG. Conserva de dónde viene y con qué licencia se puede usar, para poder citarlo.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `document_id` | entero grande, PK, generado | Sí | |
| `title` | texto | Sí | |
| `organization` | texto | No | Organismo o autor |
| `source_url` | texto | No | |
| `published_at`, `last_updated_at` | fecha | No | |
| `version` | texto | No | |
| `ingested_at` | fecha y hora | Sí | Cuándo se cargó |
| `document_type`, `authority_level` | texto | No | Tipo de documento y nivel de autoridad (organismo público, universidad, artículo...) |
| `crop`, `region`, `conditions` | texto | No | A qué cultivo, región y condiciones se aplica |
| `license` | texto | No | Licencia de uso |
| `supports` | texto | No | Parámetros o afirmaciones que permite respaldar |

### `knowledge_chunk`

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `chunk_id` | entero grande, PK, generado | Sí | |
| `document_id` | entero grande, FK → `knowledge_document` | Sí | |
| `chunk_index` | entero | Sí | Orden del fragmento dentro del documento |
| `content` | texto | Sí | |
| `section`, `page_ref` | texto | No | Apartado y página, para la cita |
| `embedding` | `vector` | No | Vector de significado. **Sin dimensión fija todavía**: depende del modelo de embeddings, sin elegir. Con pocos documentos no hace falta índice |
| `embedding_model` | texto | No | Modelo que generó el vector |

Unicidad: (`document_id`, `chunk_index`).

## 5. Relaciones

Todas son de uno a muchos. El diagrama está en [erd.md](erd.md).

| Relación | Clave foránea | Notas |
|---|---|---|
| `app_user` → `plot` | `plot.user_id` | Cada parcela pertenece a un único usuario; de ahí salen los permisos |
| `plot` → `sensor` | `sensor.plot_id` | |
| `sensor` → `sensor_reading` | `sensor_reading.sensor_id` | |
| `plot` → `forecast_data` | `forecast_data.plot_id` | |
| `plot` → `historical_weather` | `historical_weather.plot_id` | |
| `plot` → `alert` | `alert.plot_id` | |
| `plot` → `recommendation` | `recommendation.plot_id` | |
| `recommendation` → `recommendation` | `recommendation.supersedes_id` | Cadena de versiones |
| `recommendation` → `report` | `report.recommendation_id` | Obligatoria: la parcela y el periodo del informe salen de la recomendación |
| `knowledge_document` → `knowledge_chunk` | `knowledge_chunk.document_id` | |

`job_run` no se relaciona con las demás tablas.

## 6. Restricciones y disparadores

13 `CHECK`, 8 `UNIQUE` y 10 claves foráneas (las de la sección 5).

| Tipo | Dónde | Qué impone |
|---|---|---|
| `CHECK` | `app_user.email` | En minúsculas |
| `CHECK` | `plot.irrigation_efficiency` | Mayor que 0 y como máximo 1, o vacía |
| `CHECK` | `sensor.depth_cm` | Mayor que 0 |
| `CHECK` | `forecast_data.lead_days` | 0 o más |
| `CHECK` | `alert.status` | `active` o `resolved` |
| `CHECK` | `recommendation.version` | 1 o más |
| `CHECK` | `recommendation.decision` | `irrigate`, `postpone`, `do_not_irrigate` o `insufficient_data` |
| `CHECK` | `recommendation.validation_status` | `validated`, `provisional` o `insufficient_data` |
| `CHECK` | `recommendation` (periodo) | `period_end` ≥ `period_start` |
| `CHECK` | `recommendation` (cadena) | `supersedes_id` vacío si y solo si `version` = 1 |
| `CHECK` | `report.report_type` | `daily` o `weekly` |
| `CHECK` | `job_run.triggered_by` | `scheduled` o `manual` |
| `CHECK` | `job_run.status` | `running`, `success`, `partial` o `failed` |
| `UNIQUE` | `app_user.email` | Un email, una cuenta |
| `UNIQUE` | `sensor.external_code` | Varios vacíos permitidos |
| `UNIQUE` | `sensor_reading` (`sensor_id`, `measured_at`) | Sin lecturas duplicadas |
| `UNIQUE` | `forecast_data` (`plot_id`, `fecha`, `lead_days`, `source`) | |
| `UNIQUE` | `historical_weather` (`plot_id`, `fecha`) | |
| `UNIQUE` | `recommendation` (`plot_id`, `period_start`, `version`) | Una versión por parcela y día |
| `UNIQUE` | `report` (`recommendation_id`, `report_type`, `generator`, `generator_version`) | |
| `UNIQUE` | `knowledge_chunk` (`document_id`, `chunk_index`) | |
| Disparador | `recommendation` | `forbid_change`: rechaza `UPDATE` y `DELETE` (tabla inmutable) |
| Disparador | `alert` | `set_updated_at`: actualiza `updated_at` en cada modificación |

## 7. Crear y validar la base de datos

El procedimiento paso a paso está en [puesta_en_marcha.md](puesta_en_marcha.md). Resumen de lo comprobado en **PostgreSQL 17 con pgvector 0.8.7 (Docker local)**:

- Las 12 tablas se crean sin errores con `scripts/apply_schema.py`, y el script se niega a actuar si la base ya tiene tablas o si `DB_NAME` no es `irrigation_app`.
- Existen las 10 claves foráneas, los 13 `CHECK` y las 8 unicidades.
- 36 pruebas de restricciones con filas de prueba, que se limpian al terminar (no queda ninguna fila). Cubren, entre otras cosas, las restricciones de la sección 6, la inmutabilidad y la cadena de versiones de `recommendation`, la unicidad de `report` y el disparador de `alert`.
- **Inmutabilidad:** `UPDATE` y `DELETE` sobre `recommendation` fallan; crear la versión 2 apuntando a la 1 funciona.
- **pgvector:** se insertan vectores, la búsqueda por distancia (`<=>`) devuelve primero el fragmento más parecido, y un vector de otra dimensión se rechaza cuando la columna tiene dimensión fija.
- `updated_at` de `alert` se actualiza sola al modificar la fila.

Esas pruebas se ejecutaron con un script temporal que no forma parte del repositorio.

**Aiven (22 de octubre de 2026):** el servicio PostgreSQL gratuito tiene la versión 18.6 (el esquema es compatible). Se aplicó el mismo esquema con el mismo script (12 tablas, 10 claves foráneas, 13 `CHECK`, 8 unicidades, pgvector 0.8.7) y se repitieron las 36 pruebas con 0 fallos y ninguna fila sobrante.

## 8. Migración desde MySQL

El primer esquema (10 tablas, MySQL 8.4) se creó y validó en Aiven y en Docker, pero estaba vacío cuando se decidió migrar, así que no hubo que trasladar datos. Cambios principales respecto a él: `user` → `app_user`; `DATETIME` → `TIMESTAMPTZ`; JSON → JSONB; columnas meteorológicas en minúsculas; `recommendation` pasa de una fila por día a versiones inmutables con dos estados y plan de siete días; `report` cuelga de la recomendación; tablas del RAG nuevas. El contenedor local `irrigation-mysql` y su volumen se borraron el 22 de octubre de 2026. La base MySQL `irrigation_app` de Aiven, vacía y sin uso, la borra Laia desde la consola de Aiven.
