"""Datos sintéticos para las pruebas del servicio de informes (7.4): usuarios, parcelas, predicciones y previsión.

Escribe en el esquema temporal de la fixture `pg_connection` (PostgreSQL local). No hay datos reales ni de la empresa.
Las predicciones y la meteorología salen de `irrigation_engine_factory` (mismas cifras que las pruebas del motor).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from irrigation_engine_factory import CROSS_ON_DAY_4, ORIGIN
from psycopg.types.json import Jsonb

from irrigation_app.reference_plot import REFERENCE_PLOT

DEPTHS = REFERENCE_PLOT.sensor_depths_cm  # 15, 25, 35, 55


@dataclass
class Seeded:
    owner_id: int
    other_id: int
    plot_id: int  # parcela de referencia del dueño
    plot_code: str
    foreign_plot_id: int  # parcela de otra cuenta
    generic_plot_id: int  # parcela del dueño sin datos de cultivo ni de riego
    sensors: dict[int, int]  # profundidad -> sensor_id de plot_id


def add_user(connection, email: str, active: bool = True) -> int:
    return int(connection.execute(
        "INSERT INTO app_user (email, name, is_active) VALUES (%s, %s, %s) RETURNING user_id", (email, email.split("@")[0], active)
    ).fetchone()[0])


def add_plot(
    connection, user_id: int, name: str, code: str | None, *, unit: int | None, interval: int | None = None, active: bool = True
) -> tuple[int, dict[int, int]]:
    plot_id = int(connection.execute(
        "INSERT INTO plot (user_id, name, latitude, longitude, timezone, crop, plot_code, irrigation_interval_days, is_active) "
        "VALUES (%s, %s, 41.0, 0.2, 'Europe/Madrid', 'manzano', %s, %s, %s) RETURNING plot_id",
        (user_id, name, code, interval, active),
    ).fetchone()[0])
    sensors = {}
    for depth in DEPTHS:
        external = f"{unit}_{depth}" if unit is not None else None
        sensors[depth] = int(connection.execute(
            "INSERT INTO sensor (plot_id, depth_cm, external_code) VALUES (%s, %s, %s) RETURNING sensor_id", (plot_id, depth, external)
        ).fetchone()[0])
    return plot_id, sensors


def add_predictions(connection, sensors: dict[int, int], origin: dt.date = ORIGIN, levels: dict[int, float] | None = None) -> None:
    levels = levels or CROSS_ON_DAY_4
    origin_at = dt.datetime(origin.year, origin.month, origin.day, 5, tzinfo=dt.timezone.utc)
    for sensor_id in sensors.values():
        for horizon, level in levels.items():
            connection.execute(
                "INSERT INTO ml_prediction (sensor_id, origin_at, horizon_days, moisture_pred, base_moisture, method, quality, limitations) "
                "VALUES (%s, %s, %s, %s, %s, 'model', 'validated', '[]'::jsonb)",
                (sensor_id, origin_at, horizon, level, level),
            )


def add_forecast(connection, plot_id: int, origin: dt.date = ORIGIN, eto: float = 5.0, rain_by_day: dict[int, float] | None = None) -> None:
    rain_by_day = rain_by_day or {}
    captured = dt.datetime(origin.year, origin.month, origin.day, 5, tzinfo=dt.timezone.utc)
    for k in range(0, 8):
        connection.execute(
            "INSERT INTO forecast_data (plot_id, fecha, captured_at, lead_days, source, eto, rainmm) VALUES (%s, %s, %s, %s, 'test', %s, %s)",
            (plot_id, origin + dt.timedelta(days=k), captured, k, eto, rain_by_day.get(k, 0.0)),
        )


def add_models(connection) -> None:
    for horizon, mae in zip(range(1, 8), (0.66, 0.83, 1.04, 1.16, 1.29, 1.42, 1.51)):
        connection.execute(
            "INSERT INTO ml_model (algorithm, horizon_days, trained_at, data_source, moisture_unit, features, metrics, is_active) "
            "VALUES ('lightgbm', %s, now(), 'agrodevices', 'index_0_100', '{}'::jsonb, %s, TRUE)",
            (horizon, Jsonb({"typical_error": {"split": "test", "scenario": "all", "n": 1000, "mae": mae, "rmse": mae * 1.6, "bias": 0.0}})),
        )


def seed(connection, *, interval: int | None = None, origin: dt.date = ORIGIN, levels: dict[int, float] | None = None) -> Seeded:
    owner = add_user(connection, "dueno@example.com")
    other = add_user(connection, "otra@example.com")
    plot_id, sensors = add_plot(connection, owner, "1168_P2", "ABCDEFGH", unit=REFERENCE_PLOT.unit_id, interval=interval)
    foreign_id, foreign_sensors = add_plot(connection, other, "Ajena", "JKMNPQRS", unit=None)
    generic_id, generic_sensors = add_plot(connection, owner, "Otra parcela", "TUVWXYZ2", unit=None)
    for pid, sens in ((plot_id, sensors), (foreign_id, foreign_sensors), (generic_id, generic_sensors)):
        add_predictions(connection, sens, origin, levels)
        add_forecast(connection, pid, origin)
    add_models(connection)
    connection.commit()
    return Seeded(owner, other, plot_id, "ABCDEFGH", foreign_id, generic_id, sensors)
