-- Esquema de la base de datos del TFM de riego explicable.
--
-- Destino: PostgreSQL 17 con la extensión pgvector (Aiven en desarrollo, Docker en local).
-- Sustituye al esquema anterior de MySQL (migración del 22 de octubre de 2026, con la base
-- aún vacía). Diccionario de datos en docs/base_de_datos.md; decisiones en
-- docs/decisiones.md; diagrama en docs/erd.md.
--
-- Convenciones:
--   * Identificadores en inglés. Excepción acordada: las columnas meteorológicas
--     conservan los nombres de la tabla de sensores de Agrodevices. PostgreSQL pasa a
--     minúsculas los nombres sin comillas, así que tMax es tmax, rhMedia es rhmedia...
--     (son el mismo nombre sin distinguir mayúsculas).
--   * La tabla de usuarios se llama app_user porque "user" es una palabra reservada.
--   * Las columnas marcadas "PENDIENTE" existen ya, pero pueden estar vacías hasta que
--     Agrodevices o la revisión agronómica aporten los datos.
--   * No se borra: las parcelas, los usuarios y los sensores se desactivan con is_active,
--     y las claves foráneas usan el comportamiento por defecto (impedir el borrado).
--   * Fechas y horas (TIMESTAMPTZ): se guardan en UTC.
--   * Las recomendaciones son inmutables: no se modifican ni se borran, cada recálculo
--     crea una versión nueva (lo impone un trigger).


-- Búsqueda por similitud de vectores, para el RAG.
CREATE EXTENSION IF NOT EXISTS vector;


-- Mantiene updated_at al día en las tablas que lo necesitan.
CREATE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

-- Impide modificar o borrar filas de una tabla inmutable.
CREATE FUNCTION forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'La tabla % es inmutable: se crea una versión nueva en lugar de modificar o borrar', TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;


-- Cuenta de usuario. Una cuenta = una persona.
-- PENDIENTE: contraseña o identificador del proveedor de login (depende de la prueba
-- de login: Auth0 con st.login o streamlit-authenticator).
CREATE TABLE app_user (
    user_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Se guarda siempre en minúsculas: es lo que se cruza con el login.
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(150) NOT NULL,
    -- Texto libre. Con la mejora de organizaciones pasará a ser una tabla.
    company_name VARCHAR(150),
    phone VARCHAR(30),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_app_user_email_lowercase CHECK (email = lower(email))
);


-- Parcela. Pertenece a un único usuario. Las coordenadas son un atributo, no su
-- identificador. La etapa del cultivo y el Kc no se guardan: los calcula el motor a
-- partir de crop y cycle_start_date, salvo que se fijen a mano (columnas *_manual).
CREATE TABLE plot (
    plot_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES app_user(user_id),
    name VARCHAR(150) NOT NULL,
    latitude NUMERIC(9, 6) NOT NULL,
    longitude NUMERIC(9, 6) NOT NULL,
    -- Metros sobre el nivel del mar, de Open-Meteo. Vacío si se desconoce.
    altitude_m NUMERIC(7, 2),
    -- Zona horaria IANA (Europe/Madrid, Atlantic/Canary...). Define qué es "hoy".
    timezone VARCHAR(50) NOT NULL,
    -- Última consulta de alertas; compartida entre informe diario y semanal.
    -- Vacía si nunca se han consultado.
    alerts_last_seen_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- PENDIENTE de Agrodevices y de la revisión agronómica:
    crop VARCHAR(100),
    -- Siembra u otro hito que inicie el ciclo (brotación en leñosos...).
    cycle_start_date DATE,
    -- Correcciones manuales. Vacío = el motor calcula; relleno = prevalece.
    growth_stage_manual VARCHAR(100),
    kc_manual NUMERIC(5, 3),
    area_m2 NUMERIC(12, 2),
    irrigation_sector VARCHAR(100),
    soil_texture VARCHAR(100),
    -- Unidad y origen (valor típico o medido) por decidir.
    field_capacity NUMERIC(6, 3),
    wilting_point NUMERIC(6, 3),
    allowed_depletion NUMERIC(5, 3),
    -- Entre 0 y 1.
    irrigation_efficiency NUMERIC(4, 3),
    flow_rate_m3_h NUMERIC(10, 3),

    CONSTRAINT chk_plot_irrigation_efficiency
        CHECK (irrigation_efficiency IS NULL
               OR (irrigation_efficiency > 0 AND irrigation_efficiency <= 1))
);
CREATE INDEX idx_plot_user ON plot (user_id);


-- Sensor instalado en una parcela. Todos miden lo mismo y en la misma unidad (según
-- Laia; qué variable y qué unidad: PENDIENTE de confirmar con Agrodevices). Lo único
-- que varía entre ellos es la profundidad.
CREATE TABLE sensor (
    sensor_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plot_id BIGINT NOT NULL REFERENCES plot(plot_id),
    depth_cm SMALLINT NOT NULL CHECK (depth_cm > 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    -- PENDIENTE: código del sensor en Agrodevices, si existe. Varios vacíos permitidos.
    external_code VARCHAR(100) UNIQUE
);
CREATE INDEX idx_sensor_plot ON sensor (plot_id);


-- Lecturas de los sensores tal como las entrega la normalización de la empresa. Los
-- valores erróneos ya vienen interpolados con lecturas cercanas (marca SMMi frente a
-- SMM). Las tramas hexadecimales originales se guardan en la empresa, no aquí.
CREATE TABLE sensor_reading (
    reading_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sensor_id BIGINT NOT NULL REFERENCES sensor(sensor_id),
    -- Zona horaria del dato por confirmar con Agrodevices.
    measured_at TIMESTAMPTZ NOT NULL,
    -- Valor normalizado (puede estar interpolado). Tipo y unidad PENDIENTES del
    -- diccionario de Agrodevices.
    value NUMERIC(12, 4) NOT NULL,
    -- TRUE si la empresa lo interpoló (marca SMMi); FALSE si es una lectura real (SMM).
    is_interpolated BOOLEAN NOT NULL DEFAULT FALSE,
    -- Valores definitivos (ok, suspicious...) con el detector de anomalías.
    quality_flag VARCHAR(30) NOT NULL,
    quality_reason VARCHAR(255),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Evita duplicar una lectura si se vuelve a importar el mismo dato.
    UNIQUE (sensor_id, measured_at)
);


-- Previsiones meteorológicas. NUNCA se sobrescribe con el histórico: conserva lo que se
-- previó en cada captura, para evaluar después el error por antelación.
-- Unidades: las que Open-Meteo devuelve por defecto (°C, %, km/h, mm). Verificar contra
-- su documentación al implementar el conector.
CREATE TABLE forecast_data (
    forecast_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plot_id BIGINT NOT NULL REFERENCES plot(plot_id),
    -- Día previsto, en la zona horaria de la parcela.
    fecha DATE NOT NULL,
    -- Cuándo se descargó. NO equivale necesariamente a la hora de emisión del modelo.
    captured_at TIMESTAMPTZ NOT NULL,
    -- Antelación en días. Se guarda porque con la API de ejecuciones anteriores
    -- captured_at sería la fecha de descarga y no la de emisión.
    lead_days SMALLINT NOT NULL CHECK (lead_days >= 0),
    -- Endpoint u origen de la previsión (previsión normal o ejecuciones anteriores).
    source VARCHAR(50) NOT NULL,

    tmax NUMERIC(6, 2),
    tmin NUMERIC(6, 2),
    tmedia NUMERIC(6, 2),
    rhmax NUMERIC(6, 2),
    rhmin NUMERIC(6, 2),
    rhmedia NUMERIC(6, 2),
    vientomax NUMERIC(8, 3),
    vientomin NUMERIC(8, 3),
    vientomedia NUMERIC(8, 3),
    senstermicamax NUMERIC(6, 2),
    senstermicamin NUMERIC(6, 2),
    -- Evapotranspiración de referencia FAO-56, calculada por Open-Meteo (mm/día).
    eto NUMERIC(10, 2),
    rainmm NUMERIC(8, 3),
    -- Solo existen en previsiones.
    probprecipitacion NUMERIC(5, 2),
    uvmax NUMERIC(4, 2),
    -- Código WMO. La traducción al español se hace al mostrarlo.
    weather_code SMALLINT,

    UNIQUE (plot_id, fecha, lead_days, source)
);


-- Meteorología observada: reanálisis de Open-Meteo, NO medición local (esas están en
-- sensor_reading). Una fila por parcela y día, sobrescrita cuando Open-Meteo revisa los
-- valores de los últimos días. Sin UV ni probabilidad de precipitación: no existen para
-- días pasados y el UV de una previsión nunca se arrastra aquí.
CREATE TABLE historical_weather (
    historical_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plot_id BIGINT NOT NULL REFERENCES plot(plot_id),
    fecha DATE NOT NULL,
    -- Última descarga: permite informar de la antigüedad de los datos usados.
    downloaded_at TIMESTAMPTZ NOT NULL,
    -- Procedencia (reanálisis de Open-Meteo). Modelo concreto por verificar.
    source VARCHAR(50) NOT NULL,

    tmax NUMERIC(6, 2),
    tmin NUMERIC(6, 2),
    tmedia NUMERIC(6, 2),
    rhmax NUMERIC(6, 2),
    rhmin NUMERIC(6, 2),
    rhmedia NUMERIC(6, 2),
    vientomax NUMERIC(8, 3),
    vientomin NUMERIC(8, 3),
    vientomedia NUMERIC(8, 3),
    senstermicamax NUMERIC(6, 2),
    senstermicamin NUMERIC(6, 2),
    eto NUMERIC(10, 2),
    rainmm NUMERIC(8, 3),
    weather_code SMALLINT,

    UNIQUE (plot_id, fecha)
);


-- Alertas. Una fila por alerta, modificada en su sitio: las nuevas son las posteriores
-- a plot.alerts_last_seen_at, y ver una alerta no la resuelve.
CREATE TABLE alert (
    alert_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plot_id BIGINT NOT NULL REFERENCES plot(plot_id),
    -- Evita duplicados: no se crea otra si ya hay una activa del mismo tipo en la
    -- parcela. Tipos concretos con las reglas de alertas agronómicas.
    alert_type VARCHAR(50) NOT NULL,
    -- Qué ha fallado o qué se avisa.
    description TEXT NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('active', 'resolved')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Vacía mientras esté activa.
    resolved_at TIMESTAMPTZ
);
CREATE INDEX idx_alert_plot_status ON alert (plot_id, status);
CREATE TRIGGER trg_alert_updated_at BEFORE UPDATE ON alert
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- Recomendación de riego. INMUTABLE: cada recálculo crea una versión nueva vinculada a la
-- anterior (supersedes_id), y las filas nunca se modifican ni se borran. Un solo cálculo
-- contiene el plan de siete días; el informe diario usa el primer día.
-- Dos estados distintos: decision (qué se recomienda) y validation_status (cuánta
-- confianza dan los datos y el método). Estructura de los JSON provisional hasta diseñar
-- el motor y el modelo de machine learning. Sin porcentaje de confianza.
CREATE TABLE recommendation (
    recommendation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plot_id BIGINT NOT NULL REFERENCES plot(plot_id),
    -- Primer y último día del plan (el día del cálculo y seis más).
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    -- 1 en el primer cálculo del día; las versiones siguientes apuntan a la anterior.
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    supersedes_id BIGINT REFERENCES recommendation(recommendation_id),
    calculated_at TIMESTAMPTZ NOT NULL,
    -- Decisión para el primer día: regar, aplazar, no regar o datos insuficientes.
    decision VARCHAR(30) NOT NULL
        CHECK (decision IN ('irrigate', 'postpone', 'do_not_irrigate', 'insufficient_data')),
    -- validated: datos críticos y método aplicable; provisional: hay estimaciones o
    -- incertidumbres relevantes; insufficient_data: no se puede emitir dosis definitiva.
    validation_status VARCHAR(30) NOT NULL
        CHECK (validation_status IN ('validated', 'provisional', 'insufficient_data')),
    -- Plan de siete días: lámina neta y bruta en mm, volumen teórico solo si se conoce la
    -- superficie, y los datos de cada día. Los valores desconocidos van a null.
    daily_plan JSONB,
    -- Motivos estructurados (estado del suelo, demanda prevista, lluvia considerada...).
    reasons JSONB,
    -- Copia de lo que usó: parámetros de la parcela, etapa y Kc finales con su origen,
    -- datos meteorológicos, humedad prevista por el modelo de ML y su antigüedad. Cubre
    -- la reproducibilidad.
    inputs_snapshot JSONB NOT NULL,
    -- Supuestos, advertencias y limitaciones.
    limitations JSONB,
    needs_review BOOLEAN NOT NULL DEFAULT FALSE,
    method_version VARCHAR(50) NOT NULL,
    parameter_set_version VARCHAR(50) NOT NULL,
    UNIQUE (plot_id, period_start, version),
    CONSTRAINT chk_recommendation_period CHECK (period_end >= period_start),
    CONSTRAINT chk_recommendation_chain CHECK ((version = 1) = (supersedes_id IS NULL))
);
CREATE TRIGGER trg_recommendation_immutable BEFORE UPDATE OR DELETE ON recommendation
    FOR EACH ROW EXECUTE FUNCTION forbid_change();


-- Informes generados a partir de una recomendación, con el texto ya redactado. El PDF no
-- se guarda: se genera al pedirlo a partir de content y text_es. Las alertas NO se
-- guardan dentro: se consultan al abrir el informe.
CREATE TABLE report (
    report_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recommendation_id BIGINT NOT NULL REFERENCES recommendation(recommendation_id),
    report_type VARCHAR(20) NOT NULL CHECK (report_type IN ('daily', 'weekly')),
    generated_at TIMESTAMPTZ NOT NULL,
    -- Datos estructurados del informe, incluidas las fuentes citadas.
    content JSONB NOT NULL,
    -- Texto en español ya redactado; no cambia si se modifica la plantilla.
    text_es TEXT NOT NULL,
    -- Plantilla (respaldo) o modelo de lenguaje, y su versión.
    generator VARCHAR(50) NOT NULL,
    generator_version VARCHAR(50) NOT NULL,
    -- Resultado de la validación previa al PDF: cifras, unidades, secciones, referencias.
    validation_checks JSONB,
    UNIQUE (recommendation_id, report_type, generator, generator_version)
);


-- Una fila por ejecución del proceso de tareas. Sirve para saber si ya hubo un refresco
-- correcto hoy y para impedir dos ejecuciones simultáneas.
CREATE TABLE job_run (
    job_run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_name VARCHAR(50) NOT NULL,
    triggered_by VARCHAR(20) NOT NULL CHECK (triggered_by IN ('scheduled', 'manual')),
    started_at TIMESTAMPTZ NOT NULL,
    -- Vacía mientras corre.
    finished_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL
        CHECK (status IN ('running', 'success', 'partial', 'failed')),
    plots_ok INTEGER,
    plots_failed INTEGER,
    -- Nunca claves ni contraseñas.
    error_summary TEXT,
    -- Resultado por paso, para la recuperación de trabajo pendiente.
    details JSONB
);


-- Base de conocimiento agronómico para el RAG. Cada documento conserva su procedencia y
-- cada fragmento, su referencia (apartado y página) para poder citarlo.
CREATE TABLE knowledge_document (
    document_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title VARCHAR(300) NOT NULL,
    organization VARCHAR(300),
    source_url TEXT,
    published_at DATE,
    last_updated_at DATE,
    version VARCHAR(50),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Tipo de documento y nivel de autoridad (organismo público, universidad, artículo...).
    document_type VARCHAR(50),
    authority_level VARCHAR(30),
    -- A qué cultivo, región y condiciones se aplica, y su licencia de uso.
    crop VARCHAR(100),
    region VARCHAR(100),
    conditions TEXT,
    license VARCHAR(100),
    -- Parámetros o afirmaciones que permite respaldar.
    supports TEXT
);

CREATE TABLE knowledge_chunk (
    chunk_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES knowledge_document(document_id),
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    section VARCHAR(300),
    page_ref VARCHAR(50),
    -- Vector de significado del fragmento. La dimensión depende del modelo de embeddings,
    -- todavía sin elegir; con pocos documentos no hace falta índice. Se fijará después.
    embedding vector,
    embedding_model VARCHAR(100),
    UNIQUE (document_id, chunk_index)
);
