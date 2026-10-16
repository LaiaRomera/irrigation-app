# Base de datos

Referencia del modelo de datos. Complementa:
- [erd.md](erd.md): el diagrama entidad-relación.
- [`sql/create_schema.sql`](../sql/create_schema.sql): el esquema ejecutable, que es la fuente de verdad de tipos y restricciones.
- [decisiones.md](decisiones.md): por qué se decidió cada cosa.

Si cambia el esquema, hay que actualizar estos cuatro sitios.

## 1. Resumen

- **Motor:** MySQL 8.4. Base de datos `irrigation_app`, juego de caracteres `utf8mb4`.
- **10 tablas, 9 claves foráneas, 5 restricciones `CHECK` y 7 unicidades**, validadas en MySQL 8.4.11 local (Docker) y en MySQL 8.4.8 (Aiven).
- **Identificadores en inglés.** Excepción acordada: las columnas meteorológicas conservan los nombres de la tabla de sensores de Agrodevices (`tMax`, `rhMedia`, `eto`...), porque la empresa será quien use la app. Excepto `weather_code`.
- **No se borra:** usuarios, parcelas y sensores se desactivan con `is_active`. Las claves foráneas impiden el borrado de una fila a la que otras hacen referencia.
- **Fechas y horas (`DATETIME`):** se guardan en UTC. La zona horaria de las lecturas de sensores (`measured_at`) depende de cómo las entregue Agrodevices y está pendiente de confirmar. Los días (`fecha`) son en la zona horaria de la parcela.
- **Tipos orientativos:** los exactos están en el SQL.

**Estado de cada columna:** **Seguro** (acordado), **Pendiente** (columna ya creada, sin datos hasta que Agrodevices o la revisión agronómica los aporten; puede estar vacía) o **A decidir**.

## 2. Entidades

| Grupo | Tabla | Qué guarda |
|---|---|---|
| Acceso | `user` | La cuenta. Una cuenta = una persona |
| Parcela | `plot` | La parcela, sus parámetros agronómicos y el marcador de alertas vistas |
| Sensores | `sensor` | Un sensor instalado en una parcela |
| Sensores | `sensor_reading` | Cada lectura de un sensor, con su indicador de calidad |
| Meteorología | `forecast_data` | Previsiones por captura; nunca se sobrescriben |
| Meteorología | `historical_weather` | Reanálisis observado; una fila por parcela y día |
| Alertas | `alert` | Una fila por alerta, con su estado actual |
| Motor e informes | `recommendation` | Resultado del motor, con copia de lo que usó |
| Motor e informes | `report` | Informe diario o semanal ya generado |
| Operación | `job_run` | Registro de las ejecuciones de las tareas |

## 3. Decisiones de modelado

- **Cada parcela pertenece a un único usuario** (`plot.user_id`); no hay tabla de asignación. Una parcela no se puede compartir entre usuarios hasta la mejora de organizaciones, que de todos modos cambiaría el esquema.
- **Los parámetros agronómicos están dentro de `plot`** y se actualizan en su sitio, sin tabla de periodos de validez. La reproducibilidad la cubre la copia de entradas en `recommendation`.
- **Lo que se puede calcular no se guarda.** La etapa del cultivo y el Kc se deducen del cultivo y de `cycle_start_date`. Excepción acordada: `growth_stage_manual` y `kc_manual`, opcionales; vacíos significa que el motor calcula, rellenos significa que prevalecen. La profundidad radicular tampoco se guarda por ahora.
- **El marcador de alertas vistas es una columna de `plot`** (`alerts_last_seen_at`), no una tabla: al tener cada parcela un único usuario, el marcador por usuario y parcela equivale a uno por parcela. Las alertas nuevas son las de la parcela con `updated_at` posterior a ese marcador; las activas se muestran siempre.
- **`alert` es una fila por alerta, modificada en su sitio** (estado y fechas), sin tabla de historial de cambios en el MVP. Si el fallo es de un sensor, la descripción lo nombra; no hay clave foránea al sensor. Sin campo de gravedad por ahora.
- **`sensor` y `sensor_reading` son tablas separadas:** la parcela y la profundidad se guardan una sola vez, y la clave foránea impide lecturas de sensores no registrados.
- **`sensor_reading` guarda el valor normalizado por la empresa, no el original.** Los sensores envían tramas hexadecimales a la empresa, que las normaliza e interpola los valores erróneos con lecturas cercanas; las tramas originales se quedan allí. Aquí se guarda el valor y la marca `is_interpolated`, que sirve al motor y a un futuro detector de anomalías. Interpolar es habitual (unos 130 registros al mes pueden ser normales).
- **`forecast_data` es una sola tabla** (no captura más valores). Nunca se sobrescribe con el histórico.
- **`historical_weather` se sobrescribe** cuando Open-Meteo revisa los valores de los últimos días. Es reanálisis, no medición local, y no contiene UV ni probabilidad de precipitación.
- **`recommendation` guarda siempre una copia de lo que usó**, también si el resultado es "datos insuficientes". Por eso el histórico meteorológico puede sobrescribirse sin perder reproducibilidad.
- **`report` guarda el texto ya redactado** y un historial de informes generados, aunque en el MVP no se reabran los antiguos: queda preparada para los textos del LLM (columna `generator`).
- **No hay tabla de riegos** (`irrigation_event`) hasta disponer de datos de riego.
- **Campos JSON** en `recommendation`, `report` y `job_run`: provisionales en `reasons` y `limitations` hasta diseñar el motor. Requieren MySQL 5.7 o superior.

## 4. Diccionario de datos

### `user`

| Columna | Tipo | Obligatoria | Estado | Descripción |
|---|---|---|---|---|
| `user_id` | entero, PK, autoincremental | Sí | Seguro | Identificador interno |
| `email` | texto, único | Sí | Seguro | Se guarda en minúsculas. Es lo que se cruza con el login |
| `name` | texto | Sí | Seguro | Nombre para mostrar en la interfaz |
| `company_name` | texto | No | Seguro | Texto libre. Con la mejora de organizaciones pasará a ser una tabla |
| `phone` | texto | No | Seguro | Texto, para conservar prefijos y espacios |
| `is_active` | sí/no, por defecto sí | Sí | Seguro | Desactivar la cuenta sin borrarla |
| `created_at` | fecha y hora | Sí | Seguro | |
| contraseña o identificador del proveedor de login | | | Pendiente | Con Auth0 no se guardaría contraseña, solo el email. Se decide al implementar el login definitivo |

### `plot`

| Columna | Tipo | Obligatoria | Estado | Descripción |
|---|---|---|---|---|
| `plot_id` | entero, PK, autoincremental | Sí | Seguro | |
| `user_id` | entero, FK → `user` | Sí | Seguro | Dueño de la parcela |
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
| `sensor_id` | entero, PK, autoincremental | Sí | Seguro | |
| `plot_id` | entero, FK → `plot` | Sí | Seguro | Parcela donde está |
| `depth_cm` | entero | Sí | Seguro | Profundidad en centímetros. Es lo único que varía entre sensores |
| `is_active` | sí/no, por defecto sí | Sí | Seguro | |
| `external_code` | texto, único | No | Pendiente | Código del sensor en Agrodevices, si existe. Puede haber varios vacíos |

Todos los sensores miden lo mismo y en la misma unidad (según Laia). **Qué variable y qué unidad: pendiente de confirmar con Agrodevices.** Importa: si se mide tensión del suelo en lugar de humedad volumétrica, el motor lo trata de otra forma.

### `sensor_reading`

| Columna | Tipo | Obligatoria | Estado | Descripción |
|---|---|---|---|---|
| `reading_id` | entero grande, PK, autoincremental | Sí | Seguro | |
| `sensor_id` | entero, FK → `sensor` | Sí | Seguro | |
| `measured_at` | fecha y hora | Sí | Seguro | Zona horaria del dato por confirmar |
| `value` | decimal | Sí | Seguro | Valor normalizado por la empresa; puede estar interpolado. Unidad pendiente. No es el valor original: ese es la trama hexadecimal, que se guarda en la empresa |
| `is_interpolated` | sí/no, por defecto no | Sí | Seguro | Sí si la empresa lo interpoló con lecturas cercanas (marca `SMMi`); no si es una lectura real (`SMM`). Estructura exacta de la marca por confirmar con una muestra |
| `quality_flag` | texto | Sí | Seguro | Por ejemplo `ok` o `suspicious`. Los valores definitivos llegarán con el detector de anomalías, que queda fuera del MVP |
| `quality_reason` | texto | No | Seguro | Motivo si es sospechosa |
| `received_at` | fecha y hora | Sí | Seguro | Cuándo la recibimos |

Unicidad: (`sensor_id`, `measured_at`), para no duplicar una lectura si se vuelve a importar el mismo dato.

### Columnas meteorológicas comunes

Las unidades son las que Open-Meteo devuelve por defecto y que el conector antiguo no modifica. **Hay que verificarlas contra su documentación al implementar el conector.**

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
| `forecast_id` | entero grande, PK, autoincremental | Sí | |
| `plot_id` | entero, FK → `plot` | Sí | |
| `fecha` | fecha | Sí | Día previsto, en la zona horaria de la parcela |
| `captured_at` | fecha y hora | Sí | Cuándo descargamos la previsión. **No** equivale necesariamente a la hora de emisión del modelo |
| `lead_days` | entero | Sí | Antelación en días. Se guarda y no se calcula porque, con la API de ejecuciones anteriores, `captured_at` sería la fecha de descarga y no la de emisión (por verificar en la documentación de Open-Meteo) |
| `source` | texto | Sí | Endpoint u origen de la previsión (previsión normal o ejecuciones anteriores) |
| columnas meteorológicas comunes | | | Incluidas `probPrecipitacion` y `uvmax` |

Unicidad: (`plot_id`, `fecha`, `lead_days`, `source`); repetir el refresco el mismo día actualiza la fila y no la duplica.

### `historical_weather`

Una fila por parcela y día observado. Es reanálisis de Open-Meteo, **no medición local**. Se sobrescribe cuando Open-Meteo revisa los valores de los últimos días.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `historical_id` | entero grande, PK, autoincremental | Sí | |
| `plot_id` | entero, FK → `plot` | Sí | |
| `fecha` | fecha | Sí | Día observado, en la zona horaria de la parcela |
| `downloaded_at` | fecha y hora | Sí | Última descarga. Permite informar de la antigüedad de los datos usados |
| `source` | texto | Sí | Procedencia del dato (reanálisis de Open-Meteo). Modelo concreto por verificar |
| columnas meteorológicas comunes | | | Sin `probPrecipitacion` ni `uvmax` |

Unicidad: (`plot_id`, `fecha`).

### `alert`

| Columna | Tipo | Obligatoria | Estado | Descripción |
|---|---|---|---|---|
| `alert_id` | entero, PK, autoincremental | Sí | Seguro | |
| `plot_id` | entero, FK → `plot` | Sí | Seguro | |
| `alert_type` | texto | Sí | Seguro | Evita duplicados: no se crea otra si ya hay una activa del mismo tipo en la parcela. Tipos concretos con el detector |
| `description` | texto | Sí | Seguro | Qué ha fallado; si es un sensor, lo nombra |
| `status` | texto | Sí | Seguro | `active` o `resolved`. Ver una alerta no la resuelve |
| `created_at` | fecha y hora | Sí | Seguro | |
| `updated_at` | fecha y hora | Sí | Seguro | Última modificación (se actualiza sola); las alertas nuevas son las posteriores a `plot.alerts_last_seen_at` |
| `resolved_at` | fecha y hora | No | Seguro | Vacía mientras esté activa |

### `recommendation`

Una fila por parcela y día. Se calcula cuando el usuario lo solicita desde la web, con los datos guardados a las 05:00; si se pide de nuevo el mismo día, se actualiza. Las columnas salen del contrato preliminar de salidas del documento de definición del TFM y son **provisionales** hasta diseñar el motor y conocer los sensores.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `recommendation_id` | entero grande, PK, autoincremental | Sí | |
| `plot_id` | entero, FK → `plot` | Sí | |
| `calculated_at` | fecha y hora | Sí | Instante del cálculo |
| `period_start`, `period_end` | fecha | Sí | Periodo de aplicación |
| `status` | texto | Sí | Regar, aplazar, no regar o datos insuficientes. Valores definitivos con las reglas validadas |
| `net_dose_mm` | decimal | No | Dosis neta |
| `gross_dose_mm` | decimal | No | Solo si se conoce la eficiencia |
| `volume_m3` | decimal | No | Solo si se conoce la superficie |
| `duration_min` | decimal | No | Solo si se conoce un caudal representativo |
| `reasons` | JSON | No | Motivos estructurados (estado del suelo, demanda prevista, lluvia considerada, criterios aplicados). Estructura provisional |
| `inputs_snapshot` | JSON | Sí | Copia de lo que usó: parámetros de la parcela, etapa y Kc finales con su origen (calculado o manual), datos meteorológicos y de sensores y su antigüedad. Cubre la reproducibilidad |
| `limitations` | JSON | No | Supuestos y limitaciones. Estructura provisional |
| `needs_review` | sí/no | Sí | Si requiere revisión humana. Por defecto, no |
| `algorithm_version` | texto | Sí | |

Unicidad: (`plot_id`, `period_start`). Sin porcentaje de confianza: el contrato lo excluye sin una metodología que lo respalde.

### `report`

Historial de informes generados, diarios y semanales. El texto se guarda ya redactado. Las alertas **no** se guardan dentro: se consultan al abrir el informe, para que aparezcan las nuevas aunque se generara antes.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `report_id` | entero grande, PK, autoincremental | Sí | |
| `plot_id` | entero, FK → `plot` | Sí | |
| `report_type` | texto | Sí | `daily` o `weekly` |
| `period_start`, `period_end` | fecha | Sí | Periodo que cubre. El semanal distingue evolución histórica y perspectiva futura |
| `generated_at` | fecha y hora | Sí | |
| `recommendation_id` | entero, FK → `recommendation` | No | Recomendación en la que se basa el informe diario |
| `content` | JSON | Sí | Datos estructurados del informe |
| `text_es` | texto | Sí | Texto en español ya redactado. No cambia si se modifica la plantilla |
| `generator` | texto | Sí | Plantilla en el MVP; el modelo de lenguaje en el futuro |
| `generator_version` | texto | Sí | Versión de la plantilla o del modelo |

Unicidad: (`plot_id`, `report_type`, `period_start`); pedir el mismo informe el mismo día lo actualiza. Cómo se calcula la perspectiva futura del informe semanal se define con el motor.

### `job_run`

Una fila por ejecución del proceso de tareas. Sustituye al `api_log` del servicio antiguo, que registraba cada petición a Open-Meteo.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `job_run_id` | entero grande, PK, autoincremental | Sí | |
| `job_name` | texto | Sí | Por ejemplo, el refresco diario |
| `triggered_by` | texto | Sí | `scheduled` o `manual` |
| `started_at` | fecha y hora | Sí | |
| `finished_at` | fecha y hora | No | Vacía mientras corre |
| `status` | texto | Sí | `running`, `success`, `partial` o `failed` |
| `plots_ok`, `plots_failed` | entero | No | Resultado por parcelas |
| `error_summary` | texto | No | **Nunca** claves ni contraseñas |
| `details` | JSON | No | Resultado por paso, para la recuperación de trabajo pendiente |

Sirve para saber si ya hubo un refresco correcto hoy y para impedir dos ejecuciones simultáneas.

## 5. Relaciones

Todas son de uno a muchos. El diagrama está en [erd.md](erd.md).

| Relación | Clave foránea | Notas |
|---|---|---|
| `user` → `plot` | `plot.user_id` | Cada parcela pertenece a un único usuario; de ahí salen los permisos |
| `plot` → `sensor` | `sensor.plot_id` | |
| `sensor` → `sensor_reading` | `sensor_reading.sensor_id` | |
| `plot` → `forecast_data` | `forecast_data.plot_id` | |
| `plot` → `historical_weather` | `historical_weather.plot_id` | |
| `plot` → `alert` | `alert.plot_id` | |
| `plot` → `recommendation` | `recommendation.plot_id` | |
| `plot` → `report` | `report.plot_id` | |
| `recommendation` → `report` | `report.recommendation_id` | Opcional: el informe semanal abarca varias recomendaciones |

`job_run` no se relaciona con las demás tablas.

## 6. Restricciones

| Tipo | Dónde | Qué impone |
|---|---|---|
| `CHECK` | `plot.irrigation_efficiency` | Mayor que 0 y como máximo 1, o vacía |
| `CHECK` | `alert.status` | `active` o `resolved` |
| `CHECK` | `report.report_type` | `daily` o `weekly` |
| `CHECK` | `job_run.triggered_by` | `scheduled` o `manual` |
| `CHECK` | `job_run.status` | `running`, `success`, `partial` o `failed` |
| `UNIQUE` | `user.email` | Un email, una cuenta |
| `UNIQUE` | `sensor.external_code` | Varios vacíos permitidos |
| `UNIQUE` | `sensor_reading` (`sensor_id`, `measured_at`) | Sin lecturas duplicadas |
| `UNIQUE` | `forecast_data` (`plot_id`, `fecha`, `lead_days`, `source`) | |
| `UNIQUE` | `historical_weather` (`plot_id`, `fecha`) | |
| `UNIQUE` | `recommendation` (`plot_id`, `period_start`) | Una por parcela y día |
| `UNIQUE` | `report` (`plot_id`, `report_type`, `period_start`) | |

## 7. Crear y validar la base de datos

El procedimiento paso a paso está en [puesta_en_marcha.md](puesta_en_marcha.md). Resumen de lo que ya se comprobó, tanto en MySQL local como en Aiven:

- Las 10 tablas se crean sin errores con `scripts/apply_schema.py`.
- Existen las 9 claves foráneas, los 5 `CHECK` y las 7 unicidades.
- 17 pruebas con filas de prueba, todas dentro de transacciones que se revierten: rechaza el email duplicado, la parcela con usuario inexistente, la eficiencia fuera de 0–1, el estado de alerta inválido, la lectura duplicada, la previsión duplicada, el histórico duplicado, la recomendación duplicada, el JSON inválido, el tipo de informe y el estado de ejecución inválidos, y borrar un usuario con parcelas; y acepta dos sensores sin `external_code`, la misma fecha y parcela con distinta antelación y un informe diario sin recomendación asociada.

Esas pruebas se ejecutaron con un script temporal que no forma parte del repositorio.

**Cambio de `sensor_reading` del 14 de octubre de 2026:** se renombró `raw_value` a `value` y se añadió `is_interpolated`. Aplicado y comprobado en **Aiven**, con la tabla vacía: las columnas quedan en el mismo orden que en el SQL, el valor por defecto es "no", y la unicidad (`sensor_id`, `measured_at`) sigue activa. **El MySQL local de Docker conserva todavía el esquema anterior**: Docker Desktop no estaba en marcha. Cuando se encienda, hay que aplicar el mismo cambio y probar el SQL completo en una base temporal.

## 8. Compatibilidad con otras versiones de MySQL

El esquema **no** está atado al MySQL 5.6.43 de producción de la empresa. Para adaptarlo habría que:
- Guardar los campos JSON como texto, porque 5.6 no tiene el tipo JSON (se añadió en 5.7).
- Sustituir las restricciones `CHECK` por validación en código, porque en versiones anteriores a 8.0.16 MySQL las ignora.

Ambos cambios quedan pendientes hasta que la empresa decida su entorno.
