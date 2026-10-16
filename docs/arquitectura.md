# Arquitectura

Estado a 14 de octubre de 2026: **fase de diseño**. Lo único construido es el esquema de la base de datos (validado en MySQL local y en Aiven) y una prueba de login en local. El resto de módulos que se describen aquí están **planificados y todavía sin implementar**.

Cada afirmación de este documento está marcada, cuando importa, como **acordada**, **provisional** (propuesta pendiente de comprobar) o **pendiente** (dato que aún no se conoce). El registro de por qué se decidió cada cosa está en [decisiones.md](decisiones.md).

## 1. Qué se construye

Una plataforma web sencilla para una empresa que fabrica sensores de suelo agrícola (Agrodevices). Combina datos de sus sensores y parcelas con la previsión y el histórico meteorológico de Open-Meteo, para que un agrónomo o una persona de campo entienda **cuánto regar, cuándo y por qué**, a partir de una recomendación calculada y explicable.

Es un Trabajo Fin de Máster con potencial de portfolio y evolución empresarial. Se prioriza la calidad de los datos, el cálculo y la explicación, manteniendo el desarrollo web acotado.

**Alcance (acordado el 14 de octubre de 2026):** el TFM debe aplicar lo aprendido en el máster (machine learning, deep learning e IA generativa), así que los modelos son el centro del proyecto y la aplicación web es el vehículo.
- **Modelo de deep learning** que predice la humedad del suelo, para recomendar riegos de días futuros.
- **Motor determinista de riego explicable**, que decide a partir de esas predicciones.
- **Informes diario y semanal**, con una plantilla como respaldo y con explicaciones redactadas por un **modelo de lenguaje ajustado** (fine-tuning) e integrado, de forma automática.
- **Alertas agronómicas** (lluvia fuerte prevista, suelo muy seco).
- **Acceso de usuarios a sus parcelas.**

**Opcional, si da tiempo:** corrección del sesgo de la previsión meteorológica.
**Fuera del MVP:** detector de anomalías en los sensores (mejora futura).
**Descartado:** agente interactivo o chat, que un LLM calcule la dosis y usar un LLM solo con un prompt.

No se accionan válvulas ni se ejecuta riego automáticamente. No se entrena un modelo base desde cero. Los componentes de IA se describen en la sección 5.1.

## 2. Visión general

**Monolito modular (acordado):** un repositorio, un único paquete Python (`irrigation_app`) con toda la lógica, y **dos procesos** que lo comparten. No son microservicios: no se llaman entre sí por HTTP, y solo se comunican a través de la base de datos.

```mermaid
flowchart LR
    OM["Open-Meteo"] --> T
    SE["Sensores de Agrodevices"] --> T

    subgraph PT["Proceso de tareas (programado)"]
        T["Ingesta diaria a las 05:00<br/>normalización, calidad y alertas"]
    end

    T -->|escribe| DB[("MySQL")]

    subgraph PW["Proceso web (Streamlit)"]
        W["Interfaz"]
        G["Función del paquete<br/>generate_report(plot_id)"]
    end

    W -->|lee resultados y alertas| DB
    W -->|solicitar informe de hoy| G
    G -->|lee datos guardados<br/>escribe recomendación e informe| DB
```

## 3. Los dos procesos

| | Proceso de tareas | Proceso web |
|---|---|---|
| Para qué | Descargar y guardar datos | Consultar y pedir informes |
| Cuándo corre | Una vez al día, a las 05:00 | Cuando el usuario abre la web |
| Descarga datos de proveedores | **Sí** | **Nunca** |
| Ejecuta el motor de riego | No | **Sí, a petición del usuario**, para una parcela |
| Escribe en la BBDD | Previsiones, histórico, lecturas de sensores, alertas, registro de ejecuciones | Recomendaciones, informes y el marcador de alertas vistas |
| Entrena modelos | No (se hace aparte) | No |

**Acordado:**
- Abrir la web consulta resultados guardados y alertas actuales, sin llamar a proveedores, entrenar ni recalcular todo por abrir una página.
- La interfaz **no calcula**: al pulsar "solicitar informe de hoy" llama a una función del paquete (por ejemplo `generate_report(plot_id)`) que ejecuta el motor, guarda la recomendación y el informe y devuelve el resultado. La página solo lee y muestra. Esa función se ejecuta dentro del proceso web, por lo que el usuario de la BBDD de la web necesita permiso de escritura.
- Los cálculos usan los datos guardados a las 05:00. No se descarga nada al pedir un informe; un cambio de unas horas en el tiempo se considera irrelevante para decidir un riego.
- Entrenar modelos se hace aparte; la aplicación usa versiones identificadas.

Las **alertas agronómicas** se generan en la tarea diaria, tras guardar los datos. El detector de anomalías queda fuera del MVP. El texto del LLM lo genera un proceso aparte en el ordenador de Laia (ver sección 5.1).

## 4. Flujos

### 4.1. Tarea diaria (05:00)

1. Descargar la previsión de Open-Meteo para cada parcela y guardarla en `forecast_data`, **sin sobrescribir** lo anterior.
2. Descargar el histórico observado y guardarlo en `historical_weather` (se sobrescribe cuando Open-Meteo revisa los valores).
3. Importar las lecturas de los sensores a `sensor_reading`, con su marca de interpolación (`SMMi` frente a `SMM`). Los datos ya vienen normalizados por la empresa; las tramas originales se quedan allí.
4. Crear o actualizar las alertas agronómicas (lluvia fuerte prevista, suelo muy seco). Sus umbrales están pendientes.
5. Registrar la ejecución en `job_run`: éxito, fallo parcial o fallo, y qué parcelas fallaron.

Una alerta persistente **no se crea de nuevo en cada ejecución**: si ya hay una activa del mismo tipo en la parcela, se actualiza. Un fallo de una parcela no detiene al resto.

### 4.2. Abrir la web

1. Login (sección 7).
2. Se muestran las parcelas del usuario y los últimos resultados guardados, con su fecha.
3. No se descarga nada ni se recalcula.

### 4.3. Solicitar el informe de hoy

1. El usuario pulsa "solicitar informe de hoy" en una parcela suya.
2. La función del paquete comprueba el permiso en el servidor, ejecuta el motor con los datos guardados, y guarda la recomendación (una por parcela y día; si se pide otra vez, se actualiza) y el informe.
3. La página muestra el informe con la **fecha de su recomendación**. Si hoy aún no se ha pedido ninguno, se muestra el último con su fecha, junto al botón de solicitar el de hoy. **Un cálculo antiguo nunca se presenta como actual.**

### 4.4. Cuando falla una actualización

Se muestra el último resultado disponible con su fecha y avisos. Si nunca hubo resultado, se indica que no hay datos suficientes.

## 5. Módulos del paquete (planificados)

Los nombres de carpeta son **orientativos**: se crean en cada fase, no antes. Solo existe `src/irrigation_app/__init__.py`.

| Módulo | Responsabilidad | Estado |
|---|---|---|
| `connectors/` | Open-Meteo y acceso autorizado de solo lectura (o exportaciones) a los datos de Agrodevices | Por crear |
| `ingestion/` | Normalización: identificadores, unidades, fechas, procedencia y validación de estructura | Por crear |
| `storage/` | Consultas y escritura en la BBDD, sin perder las referencias a lo usado | Por crear |
| `alerts/` | Reglas de las alertas agronómicas (lluvia fuerte prevista, suelo muy seco) | Por crear; umbrales pendientes |
| `quality/` | Detector de anomalías por reglas (interpolación frecuente, sensor mudo) | **Fuera del MVP**, mejora futura |
| `models/` | Modelo de deep learning de humedad del suelo: entrenamiento (aparte) e inferencia | Por crear |
| `explanations/` | Modelo de lenguaje ajustado que redacta las explicaciones, y la comprobación de fidelidad de las cifras | Por crear |
| Motor de riego | Preparación de entradas, cálculo y construcción del resultado, en un mismo módulo | Por crear; **el nombre de la carpeta en inglés (p. ej. `irrigation_engine/`) está pendiente** |
| `reports/` | Estadísticas y plantilla diaria y semanal; futura adaptación del modelo abierto | Por crear |
| `access/` | Filtra las parcelas permitidas por usuario y sirve datos a la interfaz | Por crear |
| `jobs/` | Coordina la secuencia diaria, evita ejecuciones concurrentes y duplicados, registra resultados parciales | Por crear |
| `web/` | La interfaz Streamlit | Por crear |

**Acordado sobre el motor:** la preparación de entradas es un módulo **dentro** de la carpeta del motor, junto al cálculo y al resultado. Los conectores y los modelos quedan **fuera** y le proporcionan datos y predicciones.

El cálculo agronómico está **pendiente** de la información de los sensores y de la revisión agronómica. No se inventan columnas, unidades, calibraciones, parámetros ni umbrales.

### 5.1. Componentes de IA (acordado el 14 de octubre de 2026)

El TFM debe aplicar machine learning, deep learning e IA generativa. Se reparte el trabajo en **tres capas**: el modelo **predice**, las reglas **deciden** y el modelo de lenguaje **explica**. Ninguna IA calcula la dosis.

```mermaid
flowchart LR
    D["Lecturas de sensores<br/>y meteorología"] --> M["Modelo de deep learning<br/>predice la humedad del suelo"]
    M --> R["Motor determinista<br/>decide la recomendación"]
    D --> R
    R --> E["Modelo de lenguaje ajustado<br/>redacta la explicación"]
    E --> C{"¿Las cifras del texto<br/>coinciden con las del motor?"}
    C -->|sí| I["Informe con el texto del modelo"]
    C -->|no| P["Informe con el texto de la plantilla"]
```

**Por qué una IA no calcula la dosis.** Un LLM falla en la aritmética de varios pasos, puede dar resultados distintos con los mismos datos, no permite demostrar cómo llegó a una cifra y consume muchos más tokens. Además, un modelo supervisado necesitaría ejemplos de la dosis correcta, que no existen. El motor determinista es reproducible, auditable y gratis por cada cálculo.

**Capa 1: predicción de la humedad del suelo (modelo principal, B).**
- Deep learning de series temporales, a partir de las lecturas de los sensores y de la meteorología. Alimenta la perspectiva futura del informe semanal.
- Datos: unos 25.000 registros de Agrodevices, de varios sensores y parcelas (por verificar: cuántos sensores, frecuencia y fechas).
- Evaluación: validación temporal (se entrena con el pasado y se evalúa con el futuro, sin que el modelo vea lo que debe predecir) y comparación con baselines: persistencia, regresión sencilla y balance de agua determinista. El deep learning no tiene por qué ganar; la comparación rigurosa es el resultado.
- El entrenamiento se hace **aparte**, en el ordenador de Laia, con versiones identificadas.
- **Pendiente:** arquitectura concreta, ventana de entrada, horizonte de predicción y **dónde se guardan las predicciones**: el esquema actual no tiene una tabla para ellas y probablemente haga falta una.

**Capa 2: el motor determinista** toma la predicción y los parámetros de la parcela y decide la recomendación con reglas agronómicas (pendientes de definir).

**Capa 3: explicación con un modelo de lenguaje ajustado.**
- *Fine-tuning* de un modelo abierto pequeño con QLoRA en la GPU local (NVIDIA GeForce de 8 GB). Según guías secundarias, en 8 GB caben modelos de 3 a 4 mil millones de parámetros con holgura y de 7 a 8 mil millones muy justos; el modelo concreto, su licencia y su calidad en español están por decidir.
- Integrado y automático: una función recibe el resultado del motor, genera la explicación y la guarda en `report` (`generator` y `generator_version` identifican el modelo). Nadie conversa con él ni le escribe instrucciones.
- **Datos de entrenamiento:** pares "resultado del motor → explicación en español", creados con generación programática más generación con ayuda de una IA, revisando una muestra (idealmente con apoyo agronómico). Debe declararse en la memoria. **Dependencia:** hay que fijar antes la estructura de salida del motor, porque los ejemplos se derivan de ella.
- **Fidelidad:** el modelo genera el texto con huecos (por ejemplo `{net_dose_mm}`) que el código rellena con las cifras del motor, y una comprobación automática verifica que el texto no contradice esas cifras. Si falla, el informe usa la plantilla, que se mantiene siempre como respaldo.
- **Evaluación:** modelo ajustado frente a la plantilla y frente al mismo modelo sin ajustar, con casos reservados solo para evaluar; se miden cifras, unidades, motivos y cómo comunica lo que falta o es incierto.
- **Dónde se ejecuta:** en el ordenador de Laia, en un proceso aparte, sobre recomendaciones ya calculadas. La web, que puede estar en la nube, muestra la plantilla hasta que el texto esté disponible. Para la defensa puede ejecutarse todo en local.

**Corrección del sesgo de la previsión (A), opcional.** Si da tiempo. A un día vista el error probablemente no cambia la dosis; en los días 3 a 7 del informe semanal sí puede importar. Habría que medir cuánto cambia la dosis recomendada al corregirla.

## 6. Pantallas y navegación (acordado)

Resumen de parcelas → detalle de parcela → informe diario o semanal → historial de alertas.

- El **historial de alertas** solo es accesible desde un botón en el informe diario o semanal, con listado guardado y detalle o desplegable por incidencia. No es una sección del menú principal. Mantiene el contexto de parcela y permite volver al informe.
- Ambos informes muestran las **alertas nuevas** desde la última consulta del usuario en esa parcela, compartida entre los dos informes y guardada entre sesiones. **Ver no equivale a resolver**: las alertas activas siguen visibles.
- La primera consulta de una parcela muestra las últimas alertas y todas las activas. El número exacto de alertas recientes en esa primera consulta y el momento exacto de actualización del marcador se concretan al implementarlo.
- Los avisos nuevos se consultan al abrir el informe, aunque este se generara antes: las alertas **no** se guardan dentro del informe.
- Las alertas del MVP son **agronómicas** (lluvia fuerte prevista, suelo muy seco). Sus umbrales están pendientes. No hay aviso de interpolación: distinguir lo habitual (unos 130 registros interpolados al mes pueden ser normales) de un error exige el detector de anomalías, que queda fuera del MVP.
- El informe diario explica el estado actual, la recomendación y las alertas. El semanal distingue la evolución histórica de la perspectiva futura. Una suma semanal **no** se presenta como la orden de aplicar toda el agua de una vez.
- Junto al informe va el botón de solicitar el de hoy. El historial de recomendaciones por parcela es una mejora futura.

## 7. Acceso y seguridad

- **Autenticación (quién eres):** Auth0 mediante `st.login` de Streamlit (OIDC, con usuario y contraseña). Provisional: probado en local, pendiente de probar en Streamlit Community Cloud. Se descartó Google porque los clientes son empresas agrónomas sin por qué tener cuenta de Google. Plan B: otro alojamiento para la web o `streamlit-authenticator`.
- **Autorización (qué ves):** la da nuestra tabla `user`, cruzando el email que devuelve el login. Cuentas y parcelas **precargadas**: sin registro público ni panel de administración en el MVP. Cada parcela pertenece a un único usuario (`plot.user_id`).
- **Permisos en el servidor:** se comprueban en datos, informes e historial. Ocultar enlaces no basta.
- **Secretos:** nunca van en el código ni en git. `.env`, `.env.*` (salvo `.env.example`), `*.pem` y `.streamlit/secrets.toml` están en `.gitignore`. Los mensajes de error y los registros no deben contener claves ni contraseñas.
- **Errores:** un fallo de un proveedor se registra y no detiene el proceso. Los errores inesperados no filtran detalles internos al usuario.

## 8. Reglas del dominio

**Acordadas:**
- **Histórico y previsiones, separados.** Se conserva lo previsto en cada captura y no se sobrescribe con el histórico, para evaluar después los errores.
- **No se arrastra el UV de las previsiones al histórico.** Open-Meteo no ofrece UV ni probabilidad de precipitación para días pasados.
- **El histórico es reanálisis, no medición local.** Las mediciones locales están en `sensor_reading`. La fecha de descarga no equivale necesariamente a la fecha de emisión del modelo.
- **Datos de sensores:** llegan como tramas hexadecimales al servicio de la empresa, que las normaliza y **interpola** los valores erróneos con lecturas cercanas, marcándolos (`SMMi` frente a `SMM`). Las tramas originales se guardan en la empresa. Aquí se guarda el valor normalizado con su marca de interpolación; **los originales no se guardan en esta aplicación**.
- **Sin porcentajes de confianza** sin una metodología que los respalde.
- **No se convierte una lectura de humedad directamente a minutos de riego** sin conocer unidades, calibración, suelo, profundidad y sistema de riego. Los minutos solo se calculan si se conoce un caudal representativo.
- **Lo que se puede calcular no se guarda.** La etapa del cultivo y el Kc se calculan a partir del cultivo y de la fecha de inicio del ciclo (`cycle_start_date`), con una fuente de valores de referencia por cultivo (archivo o tabla, a decidir al diseñar el motor). El usuario puede corregirlos a mano (`growth_stage_manual`, `kc_manual`): si están vacíos, se calcula.
- **Reproducibilidad:** cada recomendación guarda una copia de lo que usó (parámetros de la parcela, etapa y Kc finales con su origen, datos meteorológicos y de sensores con su antigüedad), también cuando el resultado es "datos insuficientes".
- Los datos de entrada se usan **con su antigüedad**: la meteorología y los sensores tienen frecuencias independientes y se calcula con las últimas entradas válidas.

**Relaciones de unidades** (conversiones, no el algoritmo completo; las fórmulas y los umbrales requieren validación agronómica):
- ETc = ET0 × Kc
- Dosis bruta = dosis neta / eficiencia (eficiencia entre 0 y 1)
- Volumen (m³) = dosis bruta (mm) × superficie (m²) / 1000
- Tiempo (min) = volumen (m³) / caudal (m³/h) × 60

El algoritmo final debe incorporar el estado del suelo, la lluvia efectiva, los riegos y las pérdidas sin contar dos veces el mismo aporte.

## 9. Alojamiento y entornos

Todo debe ser gratuito durante el TFM; la empresa lo adaptará después a sus necesidades.

| Pieza | Propuesta | Estado |
|---|---|---|
| Base de datos | Aiven MySQL (plan gratuito: 1 GB, un solo nodo, se puede apagar por inactividad) | Esquema aplicado y validado en `irrigation_app` |
| Tareas diarias | GitHub Actions con cron (se programará a una hora poco habitual, porque puede retrasarse; en repos públicos se pausa tras 60 días sin actividad) | Provisional, sin implementar |
| Web | Streamlit Community Cloud (se duerme tras 12 h sin tráfico y cualquiera puede despertarla) | Provisional; el login está sin probar en la nube |
| Plan B de la web | Render (se duerme a los ~15 min; no ofrece MySQL ni Cron Jobs gratis) | Solo si Community Cloud falla |

**Entornos:**
- **Desarrollo en la nube:** base `irrigation_app` en el servicio de Aiven. Comparte servidor con la base del servicio antiguo (`defaultdb`), que no se toca.
- **Desarrollo local:** MySQL 8.4 en Docker (contenedor `irrigation-mysql`, puerto 3308). Sirve para pruebas que escriben datos sin gastar el almacenamiento de Aiven.
- **Entrenamiento y modelo de lenguaje:** en el ordenador de Laia (Intel Core i7-11370H, 16 GB de RAM, GPU NVIDIA GeForce de 8 GB, además de una Intel Iris), todo gratuito. Entorno de aprendizaje automático aparte; la compatibilidad de PyTorch con GPU y la versión de Python (3.14 frente a 3.12) están por verificar.
- **Producción:** no existe. La empresa decidirá dónde y cómo.

**Separar el TFM del despliegue empresarial.** La conclusión antigua de que ofrecer el servicio gratis a clientes equivale a uso no comercial de Open-Meteo requiere revisión: sus condiciones incluyen la integración en productos comerciales. Pendiente de revisar.

## 10. Reutilización del servicio antiguo

Del servicio meteorológico anterior (`agrodevices-meteo-app`) se integran y adaptan las piezas útiles; **no** se depende de su endpoint independiente ni se reescribe por reescribir. De él solo se han revisado `src/providers/open_meteo.py` y `sql/create_schema.sql`; el resto se conoce por su descripción.

**Se aprovecha (a verificar al implementar):**
- El conector de Open-Meteo: previsión a 7 días y reanálisis del histórico (API de archivo) por separado, pidiendo varias ubicaciones en una sola petición con coordenadas separadas por comas (la respuesta es un diccionario para una ubicación y una lista, en el mismo orden, para varias), con `timezone=auto`, validación de la estructura de la respuesta y errores que no incluyen la URL.
- La lista de variables diarias y la tabla de códigos meteorológicos WMO.

**No se hereda:**
- La API REST, el límite de peticiones, el scheduler dentro del servicio y Render.
- La tabla `parcel_config` con las coordenadas como identidad de la parcela, y `api_log`.
- La previsión sobrescrita en su sitio ni el texto del estado del cielo (aquí se guarda el código WMO).
- La conservación del UV de la previsión al pasar a día real.
- Las restricciones de MySQL 5.6 y del plan gratuito de Render.

## 11. Limitaciones y riesgos conocidos

- La arquitectura de tareas diarias y web en la nube es **provisional**: depende de pruebas aún no hechas (login en Community Cloud, retrasos del cron, umbral de inactividad de Aiven y suficiencia de 1 GB para el histórico).
- Las unidades de las columnas meteorológicas son las que Open-Meteo devuelve por defecto; hay que verificarlas al implementar el conector.
- Una corrección manual del Kc o de la etapa se queda fijada hasta que alguien la borre. En el MVP se mitiga indicando en el informe cuándo se usa un valor manual.
- Con el login en Streamlit Community Cloud hubo fallos reportados con Auth0 desde abril de 2026 (según el foro de Streamlit); hay que fijar las versiones de `streamlit[auth]` y `Authlib`.
- Sin datos de Agrodevices (qué miden los sensores, unidades, calibración, riegos, parámetros agronómicos), el motor es provisional.
- **Plazo de dos meses** para el modelo de deep learning, el fine-tuning del modelo de lenguaje, el motor y una web mínima. Va muy justo: hay que decidir pronto qué se recorta de la aplicación.
- Los 25.000 registros pueden ser pocos para deep learning según cómo estén repartidos (por sensores, parcelas y estaciones del año); se verifica al cargarlos.
- La creación de los datos de entrenamiento del modelo de lenguaje es laboriosa y depende de haber fijado antes la salida del motor.
- Un modelo de lenguaje pequeño puede alterar cifras; por eso se rellenan los huecos desde el motor y se comprueba el texto, con la plantilla como respaldo.
