-- Migración 001: tablas del modelo de humedad (ml_model y ml_prediction).
--
-- Para una base que ya tiene el esquema base: se aplica con
--     .venv/Scripts/python scripts/apply_schema.py <archivo .env> --migration sql/migrations/001_ml_tables.sql
-- Para una base nueva, estas mismas sentencias ya están al final de sql/create_schema.sql
-- (una prueba comprueba que ambos textos coinciden). Columnas decididas por Laia el 3 de
-- noviembre de 2026 (docs/decisiones.md); diccionario de datos en docs/base_de_datos.md.
-- Necesita las tablas sensor y la función forbid_change() del esquema base.

-- Un modelo entrenado: una fila por algoritmo y horizonte. Los archivos de los modelos van
-- fuera de la base (artifact_path, relativo a la raíz del proyecto).
CREATE TABLE ml_model (
    model_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    algorithm VARCHAR(20) NOT NULL CHECK (algorithm IN ('ridge', 'lightgbm', 'persistence')),
    horizon_days SMALLINT NOT NULL CHECK (horizon_days BETWEEN 1 AND 7),
    trained_at TIMESTAMPTZ NOT NULL,
    -- Primer y último día de origen de los ejemplos de entrenamiento.
    train_from DATE,
    train_to DATE,
    data_source VARCHAR(20) NOT NULL CHECK (data_source IN ('agrodevices', 'xms_cat', 'mixed')),
    -- Con qué unidad se entrenó (pendiente de confirmar con Agrodevices).
    moisture_unit VARCHAR(50) NOT NULL,
    -- Variables de entrada, en el orden que espera el modelo.
    features JSONB NOT NULL,
    hyperparameters JSONB,
    -- MAE, RMSE y sesgo del backtesting junto con la persistencia (contrato C4): de aquí
    -- sale el error típico por horizonte del informe.
    metrics JSONB,
    artifact_path VARCHAR(300),
    code_version VARCHAR(100),
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT chk_ml_model_train_period CHECK (train_to IS NULL OR train_from IS NULL OR train_to >= train_from)
);
-- Solo un modelo activo por horizonte.
CREATE UNIQUE INDEX uq_ml_model_active_per_horizon ON ml_model (horizon_days) WHERE is_active;


-- Predicción de humedad (contrato C1): una fila por sonda, momento de origen y horizonte.
-- INMUTABLE, como recommendation: no se modifica ni se borra. Repetir el cálculo de un
-- origen ya guardado no cambia la fila (INSERT ... ON CONFLICT DO NOTHING).
-- No guarda la humedad real: se compara después con sensor_reading por sensor y target_date.
CREATE TABLE ml_prediction (
    prediction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sensor_id BIGINT NOT NULL REFERENCES sensor(sensor_id),
    -- Siempre a las 05:00 UTC del día de origen.
    origin_at TIMESTAMPTZ NOT NULL,
    horizon_days SMALLINT NOT NULL CHECK (horizon_days BETWEEN 1 AND 7),
    -- Día previsto: el día UTC de origin_at más el horizonte.
    target_date DATE GENERATED ALWAYS AS (((origin_at AT TIME ZONE 'UTC')::date + horizon_days::integer)) STORED,
    -- Índice 0 a 100, ya sumado a la lectura base y recortado. Vacío solo si insufficient_data.
    moisture_pred NUMERIC(7, 3) CHECK (moisture_pred BETWEEN 0 AND 100),
    -- Última lectura válida usada como base y su fecha, para auditar y calcular su antigüedad
    -- aunque la lectura se corrija después. Vacías si no hay ninguna lectura.
    base_moisture NUMERIC(12, 4),
    base_reading_at TIMESTAMPTZ,
    -- model: lo predijo el modelo; persistence_fallback: se repite la última lectura (cambio
    -- cero). Vacío solo si insufficient_data.
    method VARCHAR(30) CHECK (method IN ('model', 'persistence_fallback')),
    quality VARCHAR(30) NOT NULL CHECK (quality IN ('validated', 'provisional', 'insufficient_data')),
    -- Lista de objetos con código y parámetros, por ejemplo [{"code": "stale_reading", "age_hours": 50}].
    limitations JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(limitations) = 'array'),
    -- Modelo activo del horizonte en el momento del cálculo (vacío si no había ninguno).
    model_id BIGINT REFERENCES ml_model(model_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (sensor_id, origin_at, horizon_days),
    CONSTRAINT chk_ml_prediction_value CHECK ((quality = 'insufficient_data') = (moisture_pred IS NULL)),
    CONSTRAINT chk_ml_prediction_method CHECK ((quality = 'insufficient_data') = (method IS NULL)),
    CONSTRAINT chk_ml_prediction_validated CHECK (quality <> 'validated' OR method = 'model')
);
CREATE INDEX idx_ml_prediction_origin ON ml_prediction (origin_at);
CREATE INDEX idx_ml_prediction_model ON ml_prediction (model_id);
CREATE TRIGGER trg_ml_prediction_immutable BEFORE UPDATE OR DELETE ON ml_prediction
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
