"""Previsión actual de Open-Meteo (tarea 8.1) sin red, y su almacén en `forecast_data` (las `integration`, en el Docker local)."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest
import requests

from irrigation_app.features import weather_features
from irrigation_app.weather import forecast_store as fs
from irrigation_app.weather.open_meteo import (
    DAILY_COLUMNS,
    FORECAST_DAYS,
    FORECAST_URL,
    RateLimitedError,
    WeatherError,
    fetch_forecast,
    fetch_previous_runs,
    forecast_to_daily,
)

FIRST_DAY = dt.date(2026, 8, 20)
VARIABLES = ("precipitation", "et0_fao_evapotranspiration", "temperature_2m", "relative_humidity_2m", "wind_speed_10m")


def forecast_response(days=8, first_day=FIRST_DAY, missing_hours=(), hours_per_day=24, scale=1.0):
    """Respuesta por horas de la previsión actual: lluvia 0,1 mm/h, ET0 0,2 mm/h, 20 °C, 60 %, 10 km/h (sin sufijos)."""
    times = [
        f"{(first_day + dt.timedelta(days=d)).isoformat()}T{h:02d}:00" for d in range(days) for h in range(hours_per_day)
    ]
    values = (0.1, 0.2, 20.0, 60.0, 10.0)
    hourly = {"time": times}
    for variable, value in zip(VARIABLES, values):
        series = [value * scale] * len(times)
        for index in missing_hours:
            series[index] = None
        hourly[variable] = series
    return {"hourly": hourly}


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code, self._body = status_code, body

    def json(self):
        if self._body is None:
            raise ValueError("sin cuerpo")
        return self._body


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        if self.error:
            raise self.error
        return self.response


# --- Conector (sin red) -------------------------------------------------------------------------

def test_the_forecast_has_eight_days_with_leads_0_to_7_counted_from_the_first_day():
    daily = forecast_to_daily(forecast_response(), FORECAST_DAYS)
    assert list(daily.columns) == DAILY_COLUMNS
    assert daily["lead_days"].tolist() == list(range(8))
    assert (daily["day"] - daily["day"].iloc[0]).dt.days.tolist() == list(range(8))
    first = daily.iloc[0]
    assert first["precipitation_mm"] == pytest.approx(2.4) and first["et0_mm"] == pytest.approx(4.8)
    assert (first["temperature_c"], first["humidity_pct"], first["wind_kmh"]) == (20, 60, 10)


def test_fetch_asks_for_current_variables_without_lead_suffix_and_in_utc():
    session = FakeSession(FakeResponse(200, forecast_response()))
    daily = fetch_forecast(41.99, 0.21, session=session)
    url, params, _ = session.calls[0]
    assert url == FORECAST_URL and params["forecast_days"] == 8 and params["timezone"] == "UTC"
    assert params["hourly"].split(",") == list(VARIABLES)  # sin «_previous_day»: es la previsión de hoy
    assert len(daily) == 8


def test_days_must_be_between_1_and_16():
    for days in (0, 17):
        with pytest.raises(ValueError):
            fetch_forecast(41.99, 0.21, days=days, session=FakeSession(FakeResponse(200, forecast_response())))


def test_an_incomplete_response_gives_fewer_rows_and_never_invented_values():
    # Un día de 23 horas (falta una) en todas las variables: ese día no se devuelve.
    daily = forecast_to_daily(forecast_response(missing_hours=[30]), FORECAST_DAYS)
    assert 1 not in daily["lead_days"].tolist() and len(daily) == 7
    # Una respuesta corta (solo 3 días): solo 3 filas, con la antelación que les toca.
    short = forecast_to_daily(forecast_response(days=3), FORECAST_DAYS)
    assert short["lead_days"].tolist() == [0, 1, 2]


def test_a_dropped_first_day_does_not_shift_the_leads_of_the_rest():
    # El día de hoy llega vacío: el de mañana sigue siendo la antelación 1, no la 0.
    daily = forecast_to_daily(forecast_response(missing_hours=list(range(24))), FORECAST_DAYS)
    assert daily["lead_days"].tolist() == [1, 2, 3, 4, 5, 6, 7]


def test_a_response_without_hourly_data_or_without_a_variable_is_an_error():
    with pytest.raises(WeatherError, match="datos por horas"):
        fetch_forecast(41.99, 0.21, session=FakeSession(FakeResponse(200, {"error": True})))
    broken = forecast_response()
    del broken["hourly"]["et0_fao_evapotranspiration"]
    with pytest.raises(WeatherError, match="et0_fao_evapotranspiration"):
        fetch_forecast(41.99, 0.21, session=FakeSession(FakeResponse(200, broken)))


def test_network_down_is_a_weather_error_without_coordinates(monkeypatch):
    monkeypatch.setattr("irrigation_app.weather.open_meteo.time.sleep", lambda seconds: None)
    session = FakeSession(error=requests.exceptions.ConnectionError("no hay red 41.99,0.21"))
    with pytest.raises(WeatherError) as caught:
        fetch_forecast(41.99, 0.21, session=session)
    assert len(session.calls) == 3  # reintenta antes de rendirse
    assert "41.99" not in str(caught.value) and "ConnectionError" in str(caught.value)


def test_rate_limit_is_not_retried(monkeypatch):
    monkeypatch.setattr("irrigation_app.weather.open_meteo.time.sleep", lambda seconds: None)
    session = FakeSession(FakeResponse(429, {"reason": "Daily API request limit exceeded"}))
    with pytest.raises(RateLimitedError, match="Daily"):
        fetch_forecast(41.99, 0.21, session=session)
    assert len(session.calls) == 1


# --- Mismo orden de magnitud que Previous Runs ----------------------------------------------------

def previous_runs_response(first_day=FIRST_DAY, scale=1.0):
    """Previous Runs para los mismos 8 días, con las antelaciones 0 a 7 (la 0 sin sufijo)."""
    base = forecast_response(first_day=first_day, scale=scale)
    hourly = {"time": base["hourly"]["time"]}
    for variable in VARIABLES:
        for lead in range(8):
            hourly[variable if lead == 0 else f"{variable}_previous_day{lead}"] = list(base["hourly"][variable])
    return {"hourly": hourly}


def test_features_from_the_current_forecast_and_from_previous_runs_agree_for_the_same_origin():
    current = forecast_to_daily(forecast_response(), FORECAST_DAYS).assign(location_id="loc")
    runs = fetch_previous_runs(
        41.99, 0.21, FIRST_DAY, FIRST_DAY + dt.timedelta(days=7), lead_days=tuple(range(8)),
        session=FakeSession(FakeResponse(200, previous_runs_response())),
    )
    archived = fs.diagonal(runs, FIRST_DAY).assign(location_id="loc")
    assert len(archived) == 8
    examples = pd.DataFrame({"unit_id": [1] * 7, "origin_day": [pd.Timestamp(FIRST_DAY, tz="UTC")] * 7, "horizon_days": range(1, 8)})
    mapping = pd.DataFrame({"unit_id": [1], "location_id": ["loc"]})
    from_current = weather_features(examples, current, mapping)
    from_archive = weather_features(examples, archived, mapping)
    pd.testing.assert_frame_equal(from_current, from_archive)
    assert not from_current["weather_incomplete"].any()


def test_a_forecast_some_percent_off_stays_in_the_same_order_of_magnitude():
    current = forecast_to_daily(forecast_response(scale=1.15), FORECAST_DAYS).assign(location_id="loc")
    runs = fetch_previous_runs(
        41.99, 0.21, FIRST_DAY, FIRST_DAY + dt.timedelta(days=7), lead_days=tuple(range(8)),
        session=FakeSession(FakeResponse(200, previous_runs_response())),
    )
    archived = fs.diagonal(runs, FIRST_DAY).assign(location_id="loc")
    examples = pd.DataFrame({"unit_id": [1] * 7, "origin_day": [pd.Timestamp(FIRST_DAY, tz="UTC")] * 7, "horizon_days": range(1, 8)})
    mapping = pd.DataFrame({"unit_id": [1], "location_id": ["loc"]})
    a, b = weather_features(examples, current, mapping), weather_features(examples, archived, mapping)
    ratio = (a["et0_sum_mm"] / b["et0_sum_mm"]).to_numpy()
    assert np.all((ratio > 0.5) & (ratio < 2))


# --- Almacén: partes que no necesitan la base de datos --------------------------------------------

def test_diagonal_keeps_only_the_day_origin_plus_k_with_lead_k():
    rows = [(pd.Timestamp(FIRST_DAY, tz="UTC") + pd.Timedelta(days=d), lead, 1.0, 1.0, 1.0, 1.0, 1.0) for d in range(4) for lead in range(4)]
    daily = pd.DataFrame(rows, columns=DAILY_COLUMNS)
    kept = fs.diagonal(daily, FIRST_DAY)
    assert kept["lead_days"].tolist() == [0, 1, 2, 3]
    assert ((kept["day"] - pd.Timestamp(FIRST_DAY, tz="UTC")).dt.days == kept["lead_days"]).all()


def test_missing_weather_reports_absent_leads_and_null_columns():
    forecast = pd.DataFrame(
        {"fecha": [FIRST_DAY, FIRST_DAY + dt.timedelta(days=1)], "lead_days": [0, 1], "rainmm": [1.0, np.nan], "eto": [4.0, 4.0],
         "tmedia": [20.0, 20.0], "rhmedia": [50.0, 50.0], "vientomedia": [8.0, 8.0], "source": ["s", "s"]}
    )
    gaps = fs.missing_weather(forecast, (0, 1, 2))
    assert gaps == {1: ["rainmm"], 2: fs.WEATHER_COLUMNS}
    assert fs.missing_weather(forecast.iloc[0:0], (0,)) == {0: fs.WEATHER_COLUMNS}
    assert fs.missing_weather(forecast, (0, 1), fs.ENGINE_COLUMNS) == {1: ["rainmm"]}


# --- Con PostgreSQL local ------------------------------------------------------------------------

def _plot(connection):
    user = connection.execute("INSERT INTO app_user (email, name) VALUES ('t@example.com', 'T') RETURNING user_id").fetchone()[0]
    plot = connection.execute(
        "INSERT INTO plot (user_id, name, latitude, longitude, timezone) VALUES (%s, 'P', 41.9, 0.2, 'Europe/Madrid') RETURNING plot_id",
        (user,),
    ).fetchone()[0]
    connection.commit()
    return plot


def _daily(skip=(), nan=None):
    rows = []
    for k in range(8):
        if k in skip:
            continue
        rows.append((pd.Timestamp(FIRST_DAY, tz="UTC") + pd.Timedelta(days=k), k, 0.5 * k, 4.0 + k / 10, 20.0, 55.0, 9.0))
    frame = pd.DataFrame(rows, columns=DAILY_COLUMNS)
    if nan:
        frame.loc[frame["lead_days"] == nan[0], nan[1]] = np.nan
    return frame


@pytest.mark.integration
def test_store_then_load_round_trips_the_c3_columns(pg_connection):
    plot = _plot(pg_connection)
    changed = fs.store_forecast(pg_connection, plot, _daily(), FIRST_DAY, fs.SOURCE_CURRENT)
    assert changed == 8
    loaded = fs.load_forecast(pg_connection, plot, FIRST_DAY)
    assert list(loaded.columns) == fs.FORECAST_COLUMNS and loaded["lead_days"].tolist() == list(range(8))
    assert (pd.to_datetime(loaded["fecha"]) - pd.Timestamp(FIRST_DAY)).dt.days.tolist() == list(range(8))
    assert loaded["rainmm"].tolist() == pytest.approx([0.5 * k for k in range(8)])
    assert loaded["eto"].iloc[3] == pytest.approx(4.3) and set(loaded["source"]) == {fs.SOURCE_CURRENT}
    # Las columnas que el proyecto no tiene se quedan en nulo, no en una cifra supuesta.
    assert pg_connection.execute("SELECT count(*) FROM forecast_data WHERE tmax IS NOT NULL OR weather_code IS NOT NULL").fetchone()[0] == 0


@pytest.mark.integration
def test_storing_twice_changes_nothing_and_keeps_the_first_values(pg_connection):
    plot = _plot(pg_connection)
    fs.store_forecast(pg_connection, plot, _daily(), FIRST_DAY, fs.SOURCE_CURRENT, dt.datetime(2026, 8, 20, 6, tzinfo=dt.timezone.utc))
    again = _daily().assign(et0_mm=99.0)  # una previsión posterior distinta
    changed = fs.store_forecast(pg_connection, plot, again, FIRST_DAY, fs.SOURCE_CURRENT, dt.datetime(2026, 8, 20, 12, tzinfo=dt.timezone.utc))
    assert changed == 0
    assert pg_connection.execute("SELECT count(*) FROM forecast_data").fetchone()[0] == 8
    biggest, first_capture = pg_connection.execute("SELECT max(eto), min(captured_at) FROM forecast_data").fetchone()
    assert float(biggest) == pytest.approx(4.7) and first_capture == dt.datetime(2026, 8, 20, 6, tzinfo=dt.timezone.utc)


@pytest.mark.integration
def test_storing_again_only_fills_the_gaps(pg_connection):
    plot = _plot(pg_connection)
    fs.store_forecast(pg_connection, plot, _daily(skip=(5, 6, 7), nan=(2, "precipitation_mm")), FIRST_DAY, fs.SOURCE_CURRENT)
    assert pg_connection.execute("SELECT count(*) FROM forecast_data").fetchone()[0] == 5
    assert fs.missing_weather(fs.load_forecast(pg_connection, plot, FIRST_DAY), tuple(range(8)))[2] == ["rainmm"]
    changed = fs.store_forecast(pg_connection, plot, _daily(), FIRST_DAY, fs.SOURCE_CURRENT)
    assert changed == 4  # la fila con hueco más las 3 que faltaban
    assert fs.missing_weather(fs.load_forecast(pg_connection, plot, FIRST_DAY), tuple(range(8))) == {}
    assert pg_connection.execute("SELECT count(*) FROM forecast_data").fetchone()[0] == 8


@pytest.mark.integration
def test_the_two_sources_do_not_mix_and_the_current_one_wins_without_a_choice(pg_connection):
    plot = _plot(pg_connection)
    fs.store_forecast(pg_connection, plot, _daily().assign(et0_mm=1.0), FIRST_DAY, fs.SOURCE_ARCHIVED)
    fs.store_forecast(pg_connection, plot, _daily().assign(et0_mm=2.0), FIRST_DAY, fs.SOURCE_CURRENT)
    assert pg_connection.execute("SELECT count(*) FROM forecast_data").fetchone()[0] == 16
    assert set(fs.load_forecast(pg_connection, plot, FIRST_DAY)["eto"]) == {2.0}
    assert set(fs.load_forecast(pg_connection, plot, FIRST_DAY, fs.SOURCE_ARCHIVED)["eto"]) == {1.0}


@pytest.mark.integration
def test_another_origin_of_the_same_dates_is_a_different_forecast(pg_connection):
    # La previsión de mañana para pasado mañana (antelación 0) no pisa la de hoy para pasado mañana (antelación 1).
    plot = _plot(pg_connection)
    fs.store_forecast(pg_connection, plot, _daily(), FIRST_DAY, fs.SOURCE_CURRENT)
    tomorrow = FIRST_DAY + dt.timedelta(days=1)
    next_day = _daily().assign(day=lambda f: f["day"] + pd.Timedelta(days=1))
    fs.store_forecast(pg_connection, plot, next_day, tomorrow, fs.SOURCE_CURRENT)
    assert pg_connection.execute("SELECT count(*) FROM forecast_data").fetchone()[0] == 16
    assert len(fs.load_forecast(pg_connection, plot, FIRST_DAY)) == 8 and len(fs.load_forecast(pg_connection, plot, tomorrow)) == 8
