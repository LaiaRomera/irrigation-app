"""Tablas ml_model y ml_prediction (tarea 4.2): textos del SQL, y restricciones en el PostgreSQL local."""

import datetime as dt
import importlib.util
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = (ROOT / "sql" / "migrations" / "001_ml_tables.sql").read_text(encoding="utf-8")
SCHEMA = (ROOT / "sql" / "create_schema.sql").read_text(encoding="utf-8")
MARKER = "-- Tablas del modelo de humedad"

_spec = importlib.util.spec_from_file_location("apply_schema", ROOT / "scripts" / "apply_schema.py")
apply_schema = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apply_schema)


def migration_body() -> str:
    return MIGRATION[MIGRATION.index("-- Un modelo entrenado"):]


def test_the_migration_creates_the_two_ml_tables():
    assert apply_schema.tables_created_by(MIGRATION) == ["ml_model", "ml_prediction"]


def test_create_schema_contains_the_migration_verbatim():
    assert migration_body() in SCHEMA


def test_the_base_schema_part_has_no_ml_tables():
    assert "ml_model" not in SCHEMA[: SCHEMA.index(MARKER)]


# --- Con PostgreSQL local ----------------------------------------------------------------------

NOW = dt.datetime(2026, 11, 5, 5, 0, tzinfo=dt.timezone.utc)


def insert_model(connection, horizon=1, algorithm="lightgbm", active=False):
    return connection.execute(
        "INSERT INTO ml_model (algorithm, horizon_days, trained_at, data_source, moisture_unit, features, is_active) "
        "VALUES (%s, %s, %s, 'agrodevices', 'relative_index_0_100', '{}', %s) RETURNING model_id",
        (algorithm, horizon, NOW, active),
    ).fetchone()[0]


def insert_sensor(connection):
    user = connection.execute("INSERT INTO app_user (email, name) VALUES ('t@example.com', 'T') RETURNING user_id").fetchone()[0]
    plot = connection.execute(
        "INSERT INTO plot (user_id, name, latitude, longitude, timezone) VALUES (%s, 'P', 41.9, 0.2, 'Europe/Madrid') RETURNING plot_id",
        (user,),
    ).fetchone()[0]
    return connection.execute("INSERT INTO sensor (plot_id, depth_cm) VALUES (%s, 15) RETURNING sensor_id", (plot,)).fetchone()[0]


def insert_prediction(connection, sensor_id, horizon=1, **overrides):
    values = {
        "moisture_pred": 60.0, "base_moisture": 61.0, "method": "model", "quality": "validated", "limitations": "[]", "model_id": None,
        "origin_at": NOW, **overrides,
    }
    return connection.execute(
        "INSERT INTO ml_prediction (sensor_id, origin_at, horizon_days, moisture_pred, base_moisture, method, quality, limitations, model_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING prediction_id",
        (sensor_id, values["origin_at"], horizon, values["moisture_pred"], values["base_moisture"], values["method"],
         values["quality"], values["limitations"], values["model_id"]),
    ).fetchone()[0]


@pytest.mark.integration
def test_only_one_active_model_per_horizon(pg_connection):
    insert_model(pg_connection, horizon=1, active=True)
    insert_model(pg_connection, horizon=2, active=True)  # otro horizonte: permitido
    insert_model(pg_connection, horizon=1, algorithm="ridge", active=False)  # inactivo: permitido
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_model(pg_connection, horizon=1, algorithm="ridge", active=True)


@pytest.mark.integration
@pytest.mark.parametrize("algorithm,horizon", [("xgboost", 1), ("lightgbm", 0), ("lightgbm", 8)])
def test_ml_model_rejects_unknown_algorithm_and_horizon_out_of_range(pg_connection, algorithm, horizon):
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_model(pg_connection, horizon=horizon, algorithm=algorithm)


@pytest.mark.integration
def test_target_date_is_the_origin_day_plus_the_horizon(pg_connection):
    sensor = insert_sensor(pg_connection)
    insert_prediction(pg_connection, sensor, horizon=3)
    assert pg_connection.execute("SELECT target_date FROM ml_prediction").fetchone()[0] == dt.date(2026, 11, 8)


@pytest.mark.integration
def test_ml_prediction_rejects_update_and_delete(pg_connection):
    sensor = insert_sensor(pg_connection)
    insert_prediction(pg_connection, sensor)
    pg_connection.commit()
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        pg_connection.execute("UPDATE ml_prediction SET moisture_pred = 1")
    pg_connection.rollback()
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        pg_connection.execute("DELETE FROM ml_prediction")


@pytest.mark.integration
def test_ml_prediction_is_unique_per_sensor_origin_and_horizon(pg_connection):
    sensor = insert_sensor(pg_connection)
    insert_prediction(pg_connection, sensor)
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_prediction(pg_connection, sensor)


@pytest.mark.integration
@pytest.mark.parametrize(
    "overrides",
    [
        {"moisture_pred": 100.5},  # fuera de 0 a 100
        {"moisture_pred": -1},
        {"moisture_pred": None, "method": None, "quality": "validated"},  # valor nulo solo con insufficient_data
        {"quality": "insufficient_data"},  # insufficient_data debe tener el valor y el método nulos
        {"quality": "validated", "method": "persistence_fallback"},  # validated exige modelo
        {"quality": "great"},
        {"limitations": '{"code": "x"}'},  # debe ser una lista
    ],
)
def test_ml_prediction_rejects_inconsistent_rows(pg_connection, overrides):
    sensor = insert_sensor(pg_connection)
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_prediction(pg_connection, sensor, **overrides)


@pytest.mark.integration
def test_ml_prediction_accepts_insufficient_data_with_null_value(pg_connection):
    sensor = insert_sensor(pg_connection)
    insert_prediction(pg_connection, sensor, moisture_pred=None, method=None, quality="insufficient_data",
                      limitations='[{"code": "no_reading"}]')
    assert pg_connection.execute("SELECT moisture_pred, method FROM ml_prediction").fetchone() == (None, None)


@pytest.mark.integration
def test_ml_prediction_needs_an_existing_sensor_and_model(pg_connection):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        insert_prediction(pg_connection, 999999)
    pg_connection.rollback()
    sensor = insert_sensor(pg_connection)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        insert_prediction(pg_connection, sensor, model_id=999999)


@pytest.mark.integration
def test_the_migration_applies_on_a_base_without_ml_tables(pg_connection):
    """La migración sola deja las mismas tablas que el esquema completo."""
    pg_connection.execute("DROP TABLE ml_prediction")
    pg_connection.execute("DROP TABLE ml_model")
    pg_connection.commit()
    created = apply_schema.apply_migration(pg_connection, ROOT / "sql" / "migrations" / "001_ml_tables.sql")
    assert created == ["ml_model", "ml_prediction"]
    insert_sensor_id = insert_sensor(pg_connection)
    insert_prediction(pg_connection, insert_sensor_id)


@pytest.mark.integration
def test_the_migration_refuses_to_run_twice(pg_connection):
    with pytest.raises(SystemExit, match="Ya existen"):
        apply_schema.apply_migration(pg_connection, ROOT / "sql" / "migrations" / "001_ml_tables.sql")
