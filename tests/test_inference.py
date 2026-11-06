"""Inferencia de la humedad (tarea 4.4) con datos sintéticos: los escalones de degradación y las variables."""

import numpy as np
import pandas as pd
import pytest

from irrigation_app.features import add_weather_features
from irrigation_app.models import inference
from irrigation_app.models.registry import LoadedModel
from irrigation_app.models.training import FEATURE_COLUMNS
from irrigation_app.scale import moisture_index
from irrigation_app.training_table import build_training_table, dawn_snapshots

ORIGIN = pd.Timestamp("2026-08-20", tz="UTC")
ORIGIN_AT = ORIGIN + pd.Timedelta(hours=5)
SENSORS = pd.DataFrame({"sensor_id": [10, 11], "depth_cm": [15, 25]})
WEATHER_COLS = ["precipitation_mm", "et0_mm", "temperature_c", "humidity_pct", "wind_kmh"]


class FixedChange:
    """Modelo de mentira: siempre predice el mismo cambio."""

    def __init__(self, change):
        self.change = change

    def predict(self, frame):
        return np.full(len(frame), self.change)


def models(change=-1.0, horizons=range(1, 8), with_ids=True):
    return {
        h: LoadedModel("lightgbm", h, FixedChange(change), list(FEATURE_COLUMNS), model_id=100 + h if with_ids else None)
        for h in horizons
    }


def weather(days=range(8), skip=()):
    """Previsión del día de origen: día origen+k con antelación k."""
    rows = [(ORIGIN + pd.Timedelta(days=k), k, 1.0, 4.0, 20.0, 50.0, 10.0) for k in days if k not in skip]
    return pd.DataFrame(rows, columns=["day", "lead_days", *WEATHER_COLS])


def readings(sensor_value=None, last=None, days_back=5):
    """Lecturas cada 15 minutos hasta `last` (por defecto, el origen más la ventana)."""
    sensor_value = sensor_value or {10: 60.0, 11: 70.0}
    last = last if last is not None else ORIGIN_AT + pd.Timedelta(minutes=15)
    stamps = pd.date_range(ORIGIN_AT - pd.Timedelta(days=days_back), last, freq="15min")
    rows = []
    for sid, base in sensor_value.items():
        for ts in stamps:
            day_offset = (ts.floor("D") - ORIGIN) / pd.Timedelta(days=1)
            rows.append((sid, 15 if sid == 10 else 25, ts, base + day_offset))  # el suelo sube 1 punto al día
    return pd.DataFrame(rows, columns=inference.READING_COLUMNS)


def predict(**kwargs):
    args = {"sensors": SENSORS, "readings": readings(), "origin_day": ORIGIN, "weather": weather(), "models": models()}
    args.update(kwargs)
    return inference.predict_origin(**args)


def pick(frame, sensor, horizon):
    return frame[(frame["sensor_id"] == sensor) & (frame["horizon_days"] == horizon)].iloc[0]


def test_step_1_fresh_reading_and_complete_weather_use_the_model():
    result = predict()
    assert len(result) == 14  # 2 sondas x 7 horizontes
    row = pick(result, 10, 3)
    assert (row["method"], row["quality"], row["limitations"]) == ("model", "validated", [])
    assert row["base_moisture"] == pytest.approx(60.0)  # lectura del amanecer del origen
    assert row["moisture_pred"] == pytest.approx(59.0)  # base + cambio del modelo (-1)
    assert row["model_id"] == 103
    assert row["origin_at"] == ORIGIN_AT
    assert row["base_reading_at"] == ORIGIN_AT + pd.Timedelta(minutes=15)


def test_readings_after_the_origin_are_ignored():
    future = readings(last=ORIGIN_AT + pd.Timedelta(days=2))
    pd.testing.assert_frame_equal(predict(readings=future), predict())


def test_step_2_missing_weather_falls_back_to_persistence_only_for_the_affected_horizons():
    result = predict(weather=weather(skip=[2]))  # falta el día origen+2: afecta a los horizontes 3 a 7
    ok, bad = pick(result, 10, 2), pick(result, 10, 3)
    assert ok["method"] == "model" and ok["quality"] == "validated"
    assert (bad["method"], bad["quality"]) == ("persistence_fallback", "provisional")
    assert bad["moisture_pred"] == pytest.approx(bad["base_moisture"])  # persistencia: cambio cero
    assert bad["limitations"][0]["code"] == "weather_missing" and bad["limitations"][0]["variables"]


def test_step_2_without_a_model_for_the_horizon_uses_persistence():
    result = predict(models=models(horizons=range(1, 7)))
    row = pick(result, 10, 7)
    assert (row["method"], row["quality"]) == ("persistence_fallback", "provisional")
    assert [item["code"] for item in row["limitations"]] == ["model_unavailable"]
    assert pd.isna(row["model_id"])


def test_step_2_fresh_reading_but_no_dawn_reading_uses_persistence_with_the_last_reading():
    gap = readings(last=ORIGIN_AT - pd.Timedelta(hours=3))  # última lectura a las 02:00, sin ventana de las 05:00
    row = pick(predict(readings=gap), 10, 1)
    assert (row["method"], row["quality"]) == ("persistence_fallback", "provisional")
    assert [item["code"] for item in row["limitations"]] == ["no_dawn_reading"]
    assert row["base_reading_at"] == ORIGIN_AT - pd.Timedelta(hours=3)


def test_step_3_a_stale_reading_gives_persistence_with_its_age():
    stale = readings(last=ORIGIN_AT - pd.Timedelta(hours=50))
    row = pick(predict(readings=stale), 10, 4)
    assert (row["method"], row["quality"]) == ("persistence_fallback", "provisional")
    assert {"code": "stale_reading", "age_hours": 50.0, "threshold_hours": 36} in row["limitations"]
    assert row["moisture_pred"] == pytest.approx(row["base_moisture"])


def test_step_4_a_reading_older_than_three_days_is_insufficient_data():
    old = readings(last=ORIGIN_AT - pd.Timedelta(hours=80))
    row = pick(predict(readings=old), 10, 1)
    assert row["quality"] == "insufficient_data" and pd.isna(row["method"])
    assert pd.isna(row["moisture_pred"]) and not pd.isna(row["base_moisture"])  # se conserva la última lectura para auditar
    assert row["limitations"][0]["code"] == "stale_reading" and row["limitations"][0]["age_hours"] == 80.0


def test_step_4_a_sensor_without_readings_is_insufficient_data_and_the_others_still_work():
    only_10 = readings(sensor_value={10: 60.0})
    result = predict(readings=only_10)
    row = pick(result, 11, 1)
    assert row["quality"] == "insufficient_data" and row["limitations"] == [{"code": "no_reading"}]
    assert pd.isna(row["base_moisture"]) and pd.isna(row["base_reading_at"])
    assert pick(result, 10, 1)["quality"] == "validated"


def test_the_level_is_clipped_to_0_100_and_says_so():
    values = {10: 98.0, 11: 5.0}
    high = predict(readings=readings(sensor_value=values), models=models(change=+10.0))
    top = pick(high, 10, 1)
    assert top["moisture_pred"] == 100.0
    assert top["limitations"] == [{"code": "clipped_to_range", "raw_value": 108.0}]
    low = predict(readings=readings(sensor_value=values), models=models(change=-10.0))
    assert pick(low, 11, 1)["moisture_pred"] == 0.0


def test_values_are_always_inside_the_contract_ranges():
    result = predict()
    assert result["moisture_pred"].between(0, 100).all()
    assert set(result["horizon_days"]) == set(range(1, 8))


# --- Las variables de inferencia son las del entrenamiento --------------------------------------

def test_inference_features_equal_the_training_features_for_the_same_example():
    unit = pd.DataFrame({"unit_id": [1], "smm_min": [6900], "smm_max": [11700]})
    rng = np.random.default_rng(0)
    stamps = pd.date_range("2026-08-10", "2026-08-30 23:45", freq="15min", tz="UTC")
    raw = pd.DataFrame(
        [
            (1, ts, depth, int(8500 + 30 * ((ts - stamps[0]) / pd.Timedelta(days=1)) + rng.integers(-20, 20)))
            for depth in (15, 25)
            for ts in stamps
        ],
        columns=["unit_id", "timestamp", "depth_cm", "frequency"],
    )
    snapshots = dawn_snapshots(raw, unit)
    table = build_training_table(snapshots)

    runs = []
    for lead in range(7):
        for k in range(-3, 14):
            day = ORIGIN + pd.Timedelta(days=k)
            runs.append(("loc", day, lead, 0.5 * k + lead, 3.0 + lead, 15.0 + k, 40.0 + lead, 5.0 + k))
    previous_runs = pd.DataFrame(runs, columns=["location_id", "day", "lead_days", *WEATHER_COLS])
    unit_locations = pd.DataFrame({"unit_id": [1], "location_id": ["loc"]})
    expected = add_weather_features(table[table["origin_day"] == ORIGIN], previous_runs, unit_locations)

    full_range = pd.Series(6900.0, index=raw.index), pd.Series(11700.0, index=raw.index)
    index_readings = raw.assign(
        value=moisture_index(raw["frequency"].astype(float), *full_range),
        sensor_id=raw["depth_cm"],  # una sonda por profundidad
    )
    sensors = pd.DataFrame({"sensor_id": [15, 25], "depth_cm": [15, 25]})
    forecast = inference.weather_for_origin(previous_runs, "loc", ORIGIN)
    got = inference.build_feature_frame(sensors, index_readings, ORIGIN, forecast)

    merged = expected.merge(got, on=["depth_cm", "horizon_days"], suffixes=("_train", "_inference"))
    assert len(merged) == len(expected) == len(got) == 14
    for column in set(FEATURE_COLUMNS) - {"depth_cm"}:
        np.testing.assert_allclose(
            merged[f"{column}_train"], merged[f"{column}_inference"], rtol=1e-9, atol=1e-9, err_msg=column
        )


def test_weather_for_origin_takes_each_day_with_its_own_lead_and_nothing_else():
    rows = [("loc", ORIGIN + pd.Timedelta(days=k), lead, 1.0, 1.0, 1.0, 1.0, 1.0) for k in range(-1, 3) for lead in range(4)]
    runs = pd.DataFrame(rows, columns=["location_id", "day", "lead_days", *WEATHER_COLS])
    got = inference.weather_for_origin(runs, "loc", ORIGIN)
    assert sorted(got["lead_days"]) == [0, 1, 2]
    assert ((got["day"] - ORIGIN) / pd.Timedelta(days=1)).tolist() == got["lead_days"].tolist()


def test_weather_from_forecast_data_renames_the_c3_columns():
    rows = pd.DataFrame(
        {"fecha": ["2026-08-20"], "lead_days": [0], "rainmm": [2.0], "eto": [5.0], "tmedia": [24.0], "rhmedia": [40.0], "vientomedia": [8.0]}
    )
    out = inference.weather_from_forecast_data(rows)
    assert list(out.columns) == inference.WEATHER_COLUMNS and out.loc[0, "et0_mm"] == 5.0


def test_forecast_days_constant_covers_the_engine_week():
    assert inference.FORECAST_DAYS == 8  # antelaciones 0 a 7: el motor usa origen+1 a origen+7


# --- Con PostgreSQL local ------------------------------------------------------------------------

def _plot_with_sensors(connection, depths=(15, 25)):
    user = connection.execute("INSERT INTO app_user (email, name) VALUES ('t@example.com', 'T') RETURNING user_id").fetchone()[0]
    plot = connection.execute(
        "INSERT INTO plot (user_id, name, latitude, longitude, timezone) VALUES (%s, 'P', 41.9, 0.2, 'Europe/Madrid') RETURNING plot_id",
        (user,),
    ).fetchone()[0]
    ids = {
        depth: connection.execute("INSERT INTO sensor (plot_id, depth_cm) VALUES (%s, %s) RETURNING sensor_id", (plot, depth)).fetchone()[0]
        for depth in depths
    }
    return plot, ids


@pytest.mark.integration
def test_predict_plot_writes_once_and_repeating_does_not_duplicate(pg_connection):
    plot, ids = _plot_with_sensors(pg_connection)
    data = readings()
    data["sensor_id"] = data["depth_cm"].map(ids)
    with pg_connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO sensor_reading (sensor_id, measured_at, value, quality_flag) VALUES (%s, %s, %s, 'ok')",
            [(int(r.sensor_id), r.timestamp.to_pydatetime(), float(r.value)) for r in data.itertuples()],
        )
    pg_connection.commit()

    first = inference.predict_plot(pg_connection, plot, ORIGIN, weather(), models(with_ids=False))
    assert len(first) == 14
    assert pg_connection.execute("SELECT count(*) FROM ml_prediction").fetchone()[0] == 14
    inference.predict_plot(pg_connection, plot, ORIGIN, weather(), models(with_ids=False))
    assert pg_connection.execute("SELECT count(*) FROM ml_prediction").fetchone()[0] == 14
    row = pg_connection.execute(
        "SELECT moisture_pred, method, quality, limitations FROM ml_prediction WHERE horizon_days = 1 AND sensor_id = %s", (ids[15],)
    ).fetchone()
    assert float(row[0]) == pytest.approx(59.0) and row[1:] == ("model", "validated", [])


@pytest.mark.integration
def test_insufficient_data_rows_are_stored_with_null_value(pg_connection):
    plot, _ = _plot_with_sensors(pg_connection, depths=(15,))
    pg_connection.commit()
    inference.predict_plot(pg_connection, plot, ORIGIN, weather(), models(with_ids=False))
    rows = pg_connection.execute("SELECT DISTINCT quality, moisture_pred, limitations FROM ml_prediction").fetchall()
    assert rows == [("insufficient_data", None, [{"code": "no_reading"}])]


def test_depth_not_in_training_uses_the_model_but_is_provisional():
    seen_15_only = {
        h: LoadedModel("lightgbm", h, FixedChange(-1.0), list(FEATURE_COLUMNS), model_id=100 + h, training_depths=(15,))
        for h in range(1, 8)
    }
    result = predict(models=seen_15_only)
    known, unseen = pick(result, 10, 2), pick(result, 11, 2)  # 15 cm vista, 25 cm no
    assert (known["method"], known["quality"], known["limitations"]) == ("model", "validated", [])
    assert (unseen["method"], unseen["quality"]) == ("model", "provisional")
    assert unseen["limitations"] == [{"code": "depth_not_in_training", "depth_cm": 25}]
    assert unseen["moisture_pred"] == pytest.approx(unseen["base_moisture"] - 1.0)  # el modelo sí se aplica


def test_unknown_training_depths_do_not_change_the_quality():
    assert (predict()["quality"] == "validated").all()  # training_depths=None: no se sabe, no se marca
