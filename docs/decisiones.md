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
- **RAG entra en el MVP**, con **pocos documentos al principio**. Deja de ser mejora futura. Pendiente: qué documentos y su licencia, y el modelo de embeddings (preferible local y gratuito).
- Validación antes de generar el PDF: las cifras y unidades coinciden con el motor, están las secciones obligatorias y las fuentes citadas existen de verdad.

**Tecnología**
- **Migración a PostgreSQL con pgvector** (decisión de Laia, 22 de octubre de 2026, que sustituye a "MySQL se mantiene"): la base de datos debe poder servir a futuros proyectos y tener búsqueda vectorial lista, y es mejor cambiarla ahora que está vacía. **Hecho el mismo día:** esquema y script reescritos, tablas del RAG añadidas y todo validado en Docker local. Aloja Aiven PostgreSQL (plan gratuito, con pgvector): servicio creado, esquema aplicado y las 36 pruebas repetidas allí sin fallos.
- **Se sigue con Streamlit, sin FastAPI en el MVP:** Streamlit llama a funciones del paquete. Pydantic sí.
- **PDF: ReportLab** (decidido el 22 de octubre de 2026 tras probar en el ordenador de Laia). WeasyPrint no arrancó en Windows porque necesita librerías gráficas del sistema (Pango/GTK) que no están instaladas; ReportLab, que es solo Python, generó un informe de prueba con tabla, gráfica de Matplotlib, tildes y símbolos como ET₀ y m³. Las fuentes estándar de PDF no tienen todos los símbolos, así que se usa una fuente Unicode (DejaVu, que trae Matplotlib).

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

### Datos de sensores
- Los sensores son de Agrodevices. Llegan **tramas hexadecimales** al servicio de la empresa, donde se **normalizan**. Las tramas originales **se guardan en la empresa**.
- Si la normalización detecta un valor erróneo (fuera de rango, etc.), lo **interpola** con lecturas cercanas (anteriores y posteriores).
- Cada lectura lleva una marca: **`SMMi` si está interpolada, `SMM` si es real** (estructura exacta por confirmar con una muestra).
- En esta aplicación se guarda el valor ya normalizado más una marca de interpolación (`is_interpolated`). **Los originales no se guardan aquí**: viven en la empresa. Esto cambia el principio de "conservar originales" para los sensores.
- Interpolar es algo habitual: en un mes puede haber unos 130 registros interpolados sin que sea un error. Referencia para el detector futuro.
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
