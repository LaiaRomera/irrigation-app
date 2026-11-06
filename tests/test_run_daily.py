"""scripts/run_daily.py (tarea 8.2): reglas sin base de datos y ejecución completa en el Docker local (`integration`)."""

import datetime as dt
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from irrigation_app.irrigation_engine.calculation import run_engine
from irrigation_app.irrigation_engine.inputs import EngineInput, SensorPrediction, WeatherDay
from irrigation_app.models.engine_feed import load_engine_feed
from irrigation_app.models.registry import LoadedModel
from irrigation_app.models.training import FEATURE_COLUMNS
from irrigation_app.reference_plot import REFERENCE_PLOT
from irrigation_app.weather import forecast_store as fs
from irrigation_app.weather.open_meteo import DAILY_COLUMNS, RateLimitedError, WeatherError

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "run_daily.py"
_spec = importlib.util.spec_from_file_location("run_daily", _PATH)
rd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rd)

ORIGIN = dt.date(2026, 8, 20)  # dentro de la campaña
ORIGIN_AT = pd.Timestamp(ORIGIN, tz="UTC") + pd.Timedelta(hours=5)
NOW = dt.datetime(2026, 8, 20, 6, 0, tzinfo=dt.timezone.utc)  # el mismo día, ya pasadas las 05:15 UTC
DEPTHS = REFERENCE_PLOT.sensor_depths_cm


# --- Reglas sin base de datos -------------------------------------------------------------------

def test_a_future_origin_or_today_before_the_window_closes_is_refused():
    with pytest.raises(rd.UsageError, match="todavía no ha ocurrido"):
        rd.validate_as_of(ORIGIN, dt.datetime(2026, 8, 20, 5, 10, tzinfo=dt.timezone.utc))
    with pytest.raises(rd.UsageError):
        rd.validate_as_of(ORIGIN + dt.timedelta(days=1), NOW)
    rd.validate_as_of(ORIGIN, dt.datetime(2026, 8, 20, 5, 15, tzinfo=dt.timezone.utc))  # no lanza
    rd.validate_as_of(ORIGIN - dt.timedelta(days=30), NOW)


def test_forecast_source_current_only_for_today():
    assert rd.choose_forecast_source(ORIGIN, ORIGIN, "auto") == fs.SOURCE_CURRENT
    assert rd.choose_forecast_source(ORIGIN - dt.timedelta(days=3), ORIGIN, "auto") == fs.SOURCE_ARCHIVED
    assert rd.choose_forecast_source(ORIGIN, ORIGIN, "archived") == fs.SOURCE_ARCHIVED
    with pytest.raises(rd.UsageError, match="solo sirve para hoy"):
        rd.choose_forecast_source(ORIGIN - dt.timedelta(days=3), ORIGIN, "current")
    with pytest.raises(rd.UsageError):
        rd.choose_forecast_source(ORIGIN, ORIGIN, "otra")


def test_campaign_warning_in_season_partly_out_and_fully_out():
    assert rd.campaign_warning(REFERENCE_PLOT, dt.date(2026, 8, 25)) is None
    partial = rd.campaign_warning(REFERENCE_PLOT, dt.date(2026, 9, 22))  # 27, 28 y 29 de septiembre ya fuera
    assert partial and "3 de los 7" in partial and "01-03 al 26-09" in partial
    winter = rd.campaign_warning(REFERENCE_PLOT, dt.date(2026, 11, 6))
    assert winter and "FUERA" in winter and "out_of_season" in winter
    assert rd.campaign_warning(REFERENCE_PLOT, dt.date(2026, 2, 20)) is not None  # antes del 1 de marzo


def test_the_alerts_step_is_an_explicit_hole():
    assert rd.alerts_step(None, 1, ORIGIN) == {"status": "not_implemented", "task": "8.4"}


# --- Con PostgreSQL local ------------------------------------------------------------------------

class FixedChange:
    def __init__(self, change):
        self.change = change

    def predict(self, frame):
        return np.full(len(frame), self.change)


def models(change=-1.0):
    return {h: LoadedModel("lightgbm", h, FixedChange(change), list(FEATURE_COLUMNS), model_id=None) for h in range(1, 8)}


def weather_daily(origin=ORIGIN, leads=range(8), skip_cols=()):
    rows = [(pd.Timestamp(origin, tz="UTC") + pd.Timedelta(days=k), k, 0.0, 5.0, 24.0, 40.0, 8.0) for k in leads]
    frame = pd.DataFrame(rows, columns=DAILY_COLUMNS)
    for column in skip_cols:
        frame[column] = np.nan
    return frame


class Fetcher:
    """Sustituye a Open-Meteo: devuelve una previsión fija, o lanza un error, y cuenta las llamadas."""

    def __init__(self, daily=None, error=None):
        self.daily, self.error, self.calls = daily, error, 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.daily


@pytest.fixture
def conn(pg_connection):
    pg_connection.autocommit = True  # run_daily abre sus propias transacciones
    return pg_connection


@pytest.fixture
def plot_id(conn):
    user = conn.execute("INSERT INTO app_user (email, name) VALUES ('t@example.com', 'T') RETURNING user_id").fetchone()[0]
    plot = conn.execute(
        "INSERT INTO plot (user_id, name, latitude, longitude, timezone) VALUES (%s, %s, 41.99, 0.21, 'Europe/Madrid') RETURNING plot_id",
        (user, REFERENCE_PLOT.name),
    ).fetchone()[0]
    rd.lrp.ensure_sensors(conn, plot, REFERENCE_PLOT)
    return plot


def add_readings(conn, plot, last=ORIGIN_AT + pd.Timedelta(minutes=15), days_back=5):
    stamps = pd.date_range(ORIGIN_AT - pd.Timedelta(days=days_back), last, freq="15min")
    sensors = conn.execute("SELECT sensor_id, depth_cm FROM sensor WHERE plot_id = %s", (plot,)).fetchall()
    rows = []
    for sensor_id, depth in sensors:
        for ts in stamps:
            offset = (ts.floor("D") - pd.Timestamp(ORIGIN, tz="UTC")) / pd.Timedelta(days=1)
            rows.append((sensor_id, ts.to_pydatetime(), 50.0 + depth / 10 + offset))  # el suelo sube 1 punto al día
    with conn.cursor() as cursor:
        cursor.executemany("INSERT INTO sensor_reading (sensor_id, measured_at, value, quality_flag) VALUES (%s, %s, %s, 'ok')", rows)


def run(conn, **kwargs):
    args = {
        "as_of": ORIGIN, "now": NOW, "skip_readings": True, "models": models(), "log": lambda text: None,
        "fetch_current": Fetcher(weather_daily()),
    }
    args.update(kwargs)
    return rd.run_daily(conn, **args)


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


@pytest.mark.integration
def test_a_normal_day_writes_seven_predictions_per_sensor_and_the_forecast(conn, plot_id):
    add_readings(conn, plot_id)
    fetcher = Fetcher(weather_daily())
    result = run(conn, fetch_current=fetcher)
    assert result["status"] == "success" and result["error"] is None and fetcher.calls == 1
    assert count(conn, "ml_prediction") == 7 * len(DEPTHS)
    per_sensor = conn.execute("SELECT count(*) FROM ml_prediction GROUP BY sensor_id").fetchall()
    assert {row[0] for row in per_sensor} == {7}
    assert conn.execute("SELECT DISTINCT method, quality FROM ml_prediction").fetchall() == [("model", "validated")]
    assert count(conn, "forecast_data") == 8
    job = conn.execute("SELECT job_name, triggered_by, status, plots_ok, plots_failed, finished_at IS NOT NULL, details FROM job_run").fetchone()
    assert job[:6] == ("run_daily", "manual", "success", 1, 0, True)
    assert job[6]["alerts"] == {"status": "not_implemented", "task": "8.4"} and job[6]["forecast"]["fetched"] is True


@pytest.mark.integration
def test_repeating_an_origin_changes_nothing_and_does_not_ask_the_weather_again(conn, plot_id):
    add_readings(conn, plot_id)
    run(conn)
    before = conn.execute("SELECT prediction_id, moisture_pred, created_at FROM ml_prediction ORDER BY prediction_id").fetchall()
    fetcher = Fetcher(error=WeatherError("no debería pedirse"))
    again = run(conn, fetch_current=fetcher, models=models(change=-5.0))  # incluso con un modelo distinto
    assert again["status"] == "success" and fetcher.calls == 0
    assert conn.execute("SELECT prediction_id, moisture_pred, created_at FROM ml_prediction ORDER BY prediction_id").fetchall() == before
    assert again["details"]["predictions"]["inserted"] == 0 and again["details"]["predictions"]["differs_from_stored"] == 7 * len(DEPTHS)
    assert count(conn, "forecast_data") == 8 and count(conn, "job_run") == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    "error", [WeatherError("Open-Meteo no respondió (ConnectionError)"), RateLimitedError("Open-Meteo respondió 429: Daily API request limit exceeded")]
)
def test_weather_unavailable_writes_persistence_with_weather_missing_and_the_run_is_partial(conn, plot_id, error):
    add_readings(conn, plot_id)
    down = Fetcher(error=error)
    result = run(conn, fetch_current=down)
    assert result["status"] == "partial" and down.calls == 1  # sin insistir
    assert "persistencia provisional" in result["error"] and str(error) in result["error"]
    assert count(conn, "ml_prediction") == 7 * len(DEPTHS)
    assert conn.execute("SELECT DISTINCT method, quality, limitations->0->>'code' FROM ml_prediction").fetchall() == [
        ("persistence_fallback", "provisional", "weather_missing")
    ]
    assert conn.execute("SELECT status, plots_ok, plots_failed FROM job_run").fetchone() == ("partial", 1, 0)
    # ml_prediction es inmutable: volver a ejecutar con red no cambia lo escrito.
    retry = run(conn)
    assert retry["details"]["predictions"]["inserted"] == 0 and count(conn, "ml_prediction") == 7 * len(DEPTHS)


@pytest.mark.integration
def test_incomplete_forecast_degrades_by_horizon(conn, plot_id):
    add_readings(conn, plot_id)
    allowed = run(conn, fetch_current=Fetcher(weather_daily(leads=range(3))))  # solo hoy y los dos días siguientes
    assert allowed["status"] == "partial"
    assert set(allowed["details"]["forecast"]["model_gaps"]) == {"3", "4", "5", "6"}
    rows = conn.execute("SELECT horizon_days, method, quality, limitations FROM ml_prediction ORDER BY horizon_days").fetchall()
    by_horizon = {h: (m, q) for h, m, q, _ in rows}
    assert {by_horizon[h] for h in (1, 2, 3)} == {("model", "validated")}
    assert {by_horizon[h] for h in (4, 5, 6, 7)} == {("persistence_fallback", "provisional")}
    assert all(l[0]["code"] == "weather_missing" for h, _, _, l in rows if h >= 4)


@pytest.mark.integration
def test_only_the_engine_day_missing_still_writes_the_model_predictions(conn, plot_id):
    add_readings(conn, plot_id)
    result = run(conn, fetch_current=Fetcher(weather_daily(leads=range(7))))  # falta origen+7, que solo usa el motor
    assert result["status"] == "partial" and "origen+7" in result["error"]
    assert conn.execute("SELECT DISTINCT method, quality FROM ml_prediction").fetchall() == [("model", "validated")]
    assert result["details"]["forecast"]["engine_gaps"] == {"7": ["eto", "rainmm"]}


@pytest.mark.integration
def test_a_reading_between_36_and_72_hours_old_is_persistence_with_stale_reading(conn, plot_id):
    add_readings(conn, plot_id, last=ORIGIN_AT - pd.Timedelta(hours=50), days_back=8)
    result = run(conn)
    assert result["status"] == "partial" and "provisionales" in result["error"]
    rows = conn.execute("SELECT DISTINCT method, quality, limitations->0->>'code' FROM ml_prediction").fetchall()
    assert rows == [("persistence_fallback", "provisional", "stale_reading")]


@pytest.mark.integration
def test_a_reading_older_than_72_hours_is_insufficient_data_without_value_and_the_run_still_finishes(conn, plot_id):
    add_readings(conn, plot_id, last=ORIGIN_AT - pd.Timedelta(hours=100), days_back=8)
    result = run(conn)
    assert result["status"] == "partial" and count(conn, "ml_prediction") == 7 * len(DEPTHS)
    assert conn.execute("SELECT DISTINCT quality, method, moisture_pred, limitations->0->>'code' FROM ml_prediction").fetchall() == [
        ("insufficient_data", None, None, "stale_reading")
    ]


@pytest.mark.integration
def test_no_readings_at_all_is_insufficient_data_without_value(conn, plot_id):
    result = run(conn)
    assert result["status"] == "partial"
    assert conn.execute("SELECT DISTINCT quality, method, moisture_pred FROM ml_prediction").fetchall() == [("insufficient_data", None, None)]


@pytest.mark.integration
def test_a_past_day_uses_the_archived_forecasts_and_not_the_current_one(conn, plot_id):
    add_readings(conn, plot_id)
    current, archived = Fetcher(error=WeatherError("no")), Fetcher(weather_daily())
    later = dt.datetime(2026, 9, 5, 9, 0, tzinfo=dt.timezone.utc)
    result = run(conn, now=later, fetch_current=current, fetch_archived=archived)
    assert result["status"] == "success" and current.calls == 0 and archived.calls == 1
    assert conn.execute("SELECT DISTINCT source FROM forecast_data").fetchall() == [(fs.SOURCE_ARCHIVED,)]
    assert count(conn, "ml_prediction") == 7 * len(DEPTHS)


@pytest.mark.integration
def test_the_feed_for_the_engine_builds_an_engine_input_and_runs(conn, plot_id):
    add_readings(conn, plot_id)
    run(conn)
    feed = load_engine_feed(conn, plot_id, ORIGIN)
    assert len(feed["predictions"]) == 7 * len(DEPTHS)
    assert [w["date"] for w in feed["weather"]] == [ORIGIN + dt.timedelta(days=k) for k in range(1, 8)]  # origen+1 a origen+7
    engine_input = EngineInput(
        plot=REFERENCE_PLOT, origin_date=feed["origin_date"],
        predictions=tuple(SensorPrediction(**p) for p in feed["predictions"]),
        weather=tuple(WeatherDay(**w) for w in feed["weather"]),
    )
    result = run_engine(engine_input)
    assert result.period_start == ORIGIN + dt.timedelta(days=1) and result.period_end == ORIGIN + dt.timedelta(days=7)
    assert len(result.plan) == 7


@pytest.mark.integration
def test_a_second_running_job_is_refused_but_a_dead_one_does_not_block(conn, plot_id):
    add_readings(conn, plot_id)
    conn.execute("INSERT INTO job_run (job_name, triggered_by, started_at, status) VALUES ('run_daily', 'manual', %s, 'running')", (NOW - dt.timedelta(minutes=5),))
    with pytest.raises(rd.UsageError, match="Ya hay una ejecución en marcha"):
        run(conn)
    assert count(conn, "ml_prediction") == 0 and count(conn, "job_run") == 1
    conn.execute("UPDATE job_run SET started_at = %s", (NOW - dt.timedelta(hours=5),))  # un cierre brusco antiguo
    assert run(conn)["status"] == "success"


@pytest.mark.integration
def test_without_the_reference_plot_it_fails_with_a_clear_message_and_leaves_the_job_closed(conn):
    with pytest.raises(rd.UsageError, match="load_reference_plot"):
        run(conn)
    assert conn.execute("SELECT status, error_summary IS NOT NULL FROM job_run").fetchone() == ("failed", True)


@pytest.mark.integration
def test_loading_readings_only_adds_the_missing_ones_and_never_duplicates(conn, plot_id, tmp_path):
    stamps = pd.date_range("2026-08-01", "2026-08-20 05:15", freq="1h", tz="UTC")
    raw = pd.DataFrame(
        [(1168, ts, depth, 9300, "SMM") for depth in DEPTHS for ts in stamps],
        columns=["unit_id", "timestamp", "depth_cm", "frequency", "reading_type"],
    )
    units = pd.DataFrame({"unit_id": [1168], "smm_min": [6900], "smm_max": [11700], "latitude": [41.99], "longitude": [0.21]})
    raw.to_parquet(tmp_path / "readings.parquet")
    units.to_parquet(tmp_path / "units.parquet")
    paths = (tmp_path / "readings.parquet", tmp_path / "units.parquet")

    first = rd.load_new_readings(conn, plot_id, REFERENCE_PLOT, *paths)
    assert first["inserted"] == len(raw) and count(conn, "sensor_reading") == len(raw)
    second = rd.load_new_readings(conn, plot_id, REFERENCE_PLOT, *paths)
    assert second["inserted"] == 0 and second["examined"] == len(raw) and count(conn, "sensor_reading") == len(raw)
    assert pd.Timestamp(second["latest_reading"]) == stamps[-1]
    # Llegan lecturas nuevas en un export posterior: solo entran las que faltan.
    later = pd.date_range("2026-08-20 06:00", "2026-08-20 08:00", freq="1h", tz="UTC")
    more = pd.DataFrame(
        [(1168, ts, depth, 9300, "SMM") for depth in DEPTHS for ts in later], columns=raw.columns
    )
    pd.concat([raw, more]).to_parquet(tmp_path / "readings.parquet")
    third = rd.load_new_readings(conn, plot_id, REFERENCE_PLOT, *paths)
    assert third["inserted"] == len(more) and pd.Timestamp(third["latest_reading"]) == later[-1]
