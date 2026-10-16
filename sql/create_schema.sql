-- Esquema de la base de datos del TFM de riego explicable.
--
-- Destino: MySQL 8.4 (Aiven en desarrollo). Diccionario de datos en
-- docs/base_de_datos.md; decisiones en docs/decisiones.md; diagrama en docs/erd.md.
--
-- Convenciones:
--   * Identificadores en inglés. Excepción acordada: las columnas meteorológicas
--     conservan los nombres de la tabla de sensores de Agrodevices (tMax, rhMedia...).
--   * Las columnas marcadas "PENDIENTE" existen ya, pero pueden estar vacías hasta que
--     Agrodevices o la revisión agronómica aporten los datos.
--   * No se borra: las parcelas, los usuarios y los sensores se desactivan con is_active,
--     y las claves foráneas usan el comportamiento por defecto (impedir el borrado).
--   * Fechas y horas (DATETIME): la zona horaria de almacenamiento está por acordar;
--     se propone UTC.
--   * Las columnas JSON requieren MySQL 5.7 o superior. El MySQL 5.6 de producción de
--     la empresa no las soporta: al adaptarlo habría que usar TEXT con JSON dentro.
--   * Las restricciones CHECK se aplican desde MySQL 8.0.16.


-- Cuenta de usuario. Una cuenta = una persona.
-- PENDIENTE: contraseña o identificador del proveedor de login (depende de la prueba
-- de login: Auth0 con st.login o streamlit-authenticator).
CREATE TABLE user (
    user_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    -- Se guarda siempre en minúsculas: es lo que se cruza con el login.
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(150) NOT NULL,
    -- Texto libre. Con la mejora de organizaciones pasará a ser una tabla.
    company_name VARCHAR(150),
    phone VARCHAR(30),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- Parcela. Pertenece a un único usuario. Las coordenadas son un atributo, no su
-- identificador. La etapa del cultivo y el Kc no se guardan: los calcula el motor a
-- partir de crop y cycle_start_date, salvo que se fijen a mano (columnas *_manual).
CREATE TABLE plot (
    plot_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    name VARCHAR(150) NOT NULL,
    latitude DECIMAL(9, 6) NOT NULL,
    longitude DECIMAL(9, 6) NOT NULL,
    -- Metros sobre el nivel del mar, de Open-Meteo. Vacío si se desconoce.
    altitude_m DECIMAL(7, 2),
    -- Zona horaria IANA (Europe/Madrid, Atlantic/Canary...). Define qué es "hoy".
    timezone VARCHAR(50) NOT NULL,
    -- Última consulta de alertas; compartida entre informe diario y semanal.
    -- Vacía si nunca se han consultado.
    alerts_last_seen_at DATETIME,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- PENDIENTE de Agrodevices y de la revisión agronómica:
    crop VARCHAR(100),
    -- Siembra u otro hito que inicie el ciclo (brotación en leñosos...).
    cycle_start_date DATE,
    -- Correcciones manuales. Vacío = el motor calcula; relleno = prevalece.
    growth_stage_manual VARCHAR(100),
    kc_manual DECIMAL(5, 3),
    area_m2 DECIMAL(12, 2),
    irrigation_sector VARCHAR(100),
    soil_texture VARCHAR(100),
    -- Unidad y origen (valor típico o medido) por decidir.
    field_capacity DECIMAL(6, 3),
    wilting_point DECIMAL(6, 3),
    allowed_depletion DECIMAL(5, 3),
    -- Entre 0 y 1.
    irrigation_efficiency DECIMAL(4, 3),
    flow_rate_m3_h DECIMAL(10, 3),

    CONSTRAINT chk_plot_irrigation_efficiency
        CHECK (irrigation_efficiency IS NULL
               OR (irrigation_efficiency > 0 AND irrigation_efficiency <= 1)),
    FOREIGN KEY (user_id) REFERENCES user(user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- Sensor instalado en una parcela. Todos miden lo mismo y en la misma unidad (según
-- Laia; qué variable y qué unidad: PENDIENTE de confirmar con Agrodevices). Lo único
-- que varía entre ellos es la profundidad.
CREATE TABLE sensor (
    sensor_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    plot_id BIGINT NOT NULL,
    depth_cm SMALLINT UNSIGNED NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    -- PENDIENTE: código del sensor en Agrodevices, si existe. Varios vacíos permitidos.
    external_code VARCHAR(100) UNIQUE,
    FOREIGN KEY (plot_id) REFERENCES plot(plot_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- Lecturas de los sensores tal como las entrega la normalización de la empresa. Los
-- valores erróneos ya vienen interpolados con lecturas cercanas (marca SMMi frente a
-- SMM). Las tramas hexadecimales originales se guardan en la empresa, no aquí.
CREATE TABLE sensor_reading (
    reading_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    sensor_id BIGINT NOT NULL,
    -- Zona horaria del dato por confirmar.
    measured_at DATETIME NOT NULL,
    -- Valor normalizado (puede estar interpolado). Tipo y unidad PENDIENTES del
    -- diccionario de Agrodevices.
    value DECIMAL(12, 4) NOT NULL,
    -- TRUE si la empresa lo interpoló (marca SMMi); FALSE si es una lectura real (SMM).
    is_interpolated BOOLEAN NOT NULL DEFAULT FALSE,
    -- Valores definitivos (ok, suspicious...) con el detector de anomalías.
    quality_flag VARCHAR(30) NOT NULL,
    quality_reason VARCHAR(255),
    received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Evita duplicar una lectura si se vuelve a importar el mismo dato.
    UNIQUE (sensor_id, measured_at),
    FOREIGN KEY (sensor_id) REFERENCES sensor(sensor_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- Previsiones meteorológicas. NUNCA se sobrescribe con el histórico: conserva lo que se
-- previó en cada captura, para evaluar después el error por antelación.
-- Unidades: las que Open-Meteo devuelve por defecto (°C, %, km/h, mm). Verificar contra
-- su documentación al implementar el conector.
CREATE TABLE forecast_data (
    forecast_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    plot_id BIGINT NOT NULL,
    -- Día previsto, en la zona horaria de la parcela.
    fecha DATE NOT NULL,
    -- Cuándo se descargó. NO equivale necesariamente a la hora de emisión del modelo.
    captured_at DATETIME NOT NULL,
    -- Antelación en días. Se guarda porque con la API de ejecuciones anteriores
    -- captured_at sería la fecha de descarga y no la de emisión.
    lead_days TINYINT UNSIGNED NOT NULL,
    -- Endpoint u origen de la previsión (previsión normal o ejecuciones anteriores).
    source VARCHAR(50) NOT NULL,

    tMax DECIMAL(6, 2),
    tMin DECIMAL(6, 2),
    tMedia DECIMAL(6, 2),
    rhMax DECIMAL(6, 2),
    rhMin DECIMAL(6, 2),
    rhMedia DECIMAL(6, 2),
    vientoMax DECIMAL(8, 3),
    vientoMin DECIMAL(8, 3),
    vientoMedia DECIMAL(8, 3),
    sensTermicaMax DECIMAL(6, 2),
    sensTermicaMin DECIMAL(6, 2),
    -- Evapotranspiración de referencia FAO-56, calculada por Open-Meteo (mm/día).
    eto DECIMAL(10, 2),
    rainMm DECIMAL(8, 3),
    -- Solo existen en previsiones.
    probPrecipitacion DECIMAL(5, 2),
    uvmax DECIMAL(4, 2),
    -- Código WMO. La traducción al español se hace al mostrarlo.
    weather_code SMALLINT,

    UNIQUE (plot_id, fecha, lead_days, source),
    FOREIGN KEY (plot_id) REFERENCES plot(plot_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- Meteorología observada: reanálisis de Open-Meteo, NO medición local (esas están en
-- sensor_reading). Una fila por parcela y día, sobrescrita cuando Open-Meteo revisa los
-- valores de los últimos días. Sin UV ni probabilidad de precipitación: no existen para
-- días pasados y el UV de una previsión nunca se arrastra aquí.
CREATE TABLE historical_weather (
    historical_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    plot_id BIGINT NOT NULL,
    fecha DATE NOT NULL,
    -- Última descarga: permite informar de la antigüedad de los datos usados.
    downloaded_at DATETIME NOT NULL,
    -- Procedencia (reanálisis de Open-Meteo). Modelo concreto por verificar.
    source VARCHAR(50) NOT NULL,

    tMax DECIMAL(6, 2),
    tMin DECIMAL(6, 2),
    tMedia DECIMAL(6, 2),
    rhMax DECIMAL(6, 2),
    rhMin DECIMAL(6, 2),
    rhMedia DECIMAL(6, 2),
    vientoMax DECIMAL(8, 3),
    vientoMin DECIMAL(8, 3),
    vientoMedia DECIMAL(8, 3),
    sensTermicaMax DECIMAL(6, 2),
    sensTermicaMin DECIMAL(6, 2),
    eto DECIMAL(10, 2),
    rainMm DECIMAL(8, 3),
    weather_code SMALLINT,

    UNIQUE (plot_id, fecha),
    FOREIGN KEY (plot_id) REFERENCES plot(plot_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- Alertas. Una fila por alerta, modificada en su sitio: las nuevas son las posteriores
-- a plot.alerts_last_seen_at, y ver una alerta no la resuelve.
CREATE TABLE alert (
    alert_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    plot_id BIGINT NOT NULL,
    -- Evita duplicados: no se crea otra si ya hay una activa del mismo tipo en la
    -- parcela. Tipos concretos con el detector de anomalías.
    alert_type VARCHAR(50) NOT NULL,
    -- Qué ha fallado; si es un sensor, lo nombra.
    description TEXT NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    -- Vacía mientras esté activa.
    resolved_at DATETIME,
    CONSTRAINT chk_alert_status CHECK (status IN ('active', 'resolved')),
    INDEX idx_alert_plot_status (plot_id, status),
    FOREIGN KEY (plot_id) REFERENCES plot(plot_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- Recomendación de riego. Una por parcela y día; se calcula cuando el usuario la pide
-- desde la web, con los datos guardados a las 05:00, y se actualiza si se pide de nuevo.
-- Columnas PROVISIONALES hasta diseñar el motor y conocer los sensores.
-- Sin porcentaje de confianza: el contrato lo excluye sin una metodología que lo respalde.
CREATE TABLE recommendation (
    recommendation_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    plot_id BIGINT NOT NULL,
    calculated_at DATETIME NOT NULL,
    -- Periodo de aplicación.
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    -- Regar, aplazar, no regar o datos insuficientes. Valores definitivos con las
    -- reglas validadas.
    status VARCHAR(30) NOT NULL,
    net_dose_mm DECIMAL(8, 3),
    -- Solo si se conoce la eficiencia.
    gross_dose_mm DECIMAL(8, 3),
    -- Solo si se conoce la superficie.
    volume_m3 DECIMAL(12, 3),
    -- Solo si se conoce un caudal representativo.
    duration_min DECIMAL(8, 2),
    -- Motivos estructurados: estado del suelo, demanda prevista, lluvia considerada,
    -- criterios aplicados. Estructura provisional.
    reasons JSON,
    -- Copia de lo que usó: parámetros de la parcela, etapa y Kc finales con su origen
    -- (calculado o manual), datos meteorológicos y de sensores y su antigüedad. Cubre la
    -- reproducibilidad, por eso el histórico meteorológico puede sobrescribirse.
    inputs_snapshot JSON NOT NULL,
    -- Supuestos y limitaciones. Estructura provisional.
    limitations JSON,
    needs_review BOOLEAN NOT NULL DEFAULT FALSE,
    algorithm_version VARCHAR(50) NOT NULL,
    UNIQUE (plot_id, period_start),
    FOREIGN KEY (plot_id) REFERENCES plot(plot_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- Historial de informes generados, diarios y semanales, con el texto ya redactado.
-- Las alertas NO se guardan dentro: se consultan al abrir el informe.
CREATE TABLE report (
    report_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    plot_id BIGINT NOT NULL,
    report_type VARCHAR(20) NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    generated_at DATETIME NOT NULL,
    -- Recomendación en la que se basa el informe diario. El semanal abarca varias.
    recommendation_id BIGINT,
    -- Datos estructurados del informe.
    content JSON NOT NULL,
    -- Texto en español ya redactado; no cambia si se modifica la plantilla.
    text_es TEXT NOT NULL,
    -- Plantilla en el MVP; el modelo de lenguaje en el futuro.
    generator VARCHAR(50) NOT NULL,
    generator_version VARCHAR(50) NOT NULL,
    CONSTRAINT chk_report_type CHECK (report_type IN ('daily', 'weekly')),
    UNIQUE (plot_id, report_type, period_start),
    FOREIGN KEY (plot_id) REFERENCES plot(plot_id),
    FOREIGN KEY (recommendation_id) REFERENCES recommendation(recommendation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- Una fila por ejecución del proceso de tareas. Sirve para saber si ya hubo un refresco
-- correcto hoy y para impedir dos ejecuciones simultáneas.
CREATE TABLE job_run (
    job_run_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    job_name VARCHAR(50) NOT NULL,
    triggered_by VARCHAR(20) NOT NULL,
    started_at DATETIME NOT NULL,
    -- Vacía mientras corre.
    finished_at DATETIME,
    status VARCHAR(20) NOT NULL,
    plots_ok INT,
    plots_failed INT,
    -- Nunca claves ni contraseñas.
    error_summary TEXT,
    -- Resultado por paso, para la recuperación de trabajo pendiente.
    details JSON,
    CONSTRAINT chk_job_run_triggered_by CHECK (triggered_by IN ('scheduled', 'manual')),
    CONSTRAINT chk_job_run_status
        CHECK (status IN ('running', 'success', 'partial', 'failed'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
