"""Pruebas del conector de Open-Meteo, sin red (respuestas sintéticas)."""

from datetime import date, timedelta

import pandas as pd
import pytest
import requests

from irrigation_app.weather.open_meteo import (
    DAILY_COLUMNS,
    RateLimitedError,
    WeatherError,
    fetch_previous_runs,
    previous_runs_to_daily,
)


def make_response(days=2, lead_days=(1,), missing_hours=0, empty_first_day=False):
    """Respuesta por horas: lluvia 0,1 mm/h, ET₀ 0,05 mm/h, temperatura 20, humedad 60, viento 10."""
    times = [f"2026-01-{d:02d}T{h:02d}:00" for d in range(1, days + 1) for h in range(24)]
    hourly = {"time": times}
    for lead in lead_days:
        for variable, value in (
            ("precipitation", 0.1), ("et0_fao_evapotranspiration", 0.05), ("temperature_2m", 20.0),
            ("relative_humidity_2m", 60.0), ("wind_speed_10m", 10.0),
        ):
            values = [value] * len(times)
            if empty_first_day:
                values[:24] = [None] * 24
            for index in range(missing_hours):
                values[30 + index] = None
            # La antelación 0 viene con el nombre de la variable a secas.
            hourly[variable if lead == 0 else f"{variable}_previous_day{lead}"] = values
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


def test_hours_are_summed_or_averaged_by_variable():
    daily = previous_runs_to_daily(make_response(), (1,))
    assert list(daily.columns) == DAILY_COLUMNS
    first = daily.iloc[0]
    assert first["precipitation_mm"] == pytest.approx(2.4)
    assert first["et0_mm"] == pytest.approx(1.2)
    assert (first["temperature_c"], first["humidity_pct"], first["wind_kmh"]) == (20, 60, 10)
    assert first["lead_days"] == 1 and str(first["day"].tz) == "UTC"


def test_a_day_with_a_missing_hour_in_one_variable_has_no_value_for_it_only():
    response = make_response()
    response["hourly"]["precipitation_previous_day1"][30] = None
    daily = previous_runs_to_daily(response, (1,))
    daily = daily.set_index(daily["day"].dt.day)
    assert daily.loc[1, "precipitation_mm"] == pytest.approx(2.4)
    assert pd.isna(daily.loc[2, "precipitation_mm"]) and daily.loc[2, "et0_mm"] == pytest.approx(1.2)


def test_a_day_with_an_hour_missing_in_every_variable_is_not_returned():
    daily = previous_runs_to_daily(make_response(missing_hours=1), (1,))
    assert list(daily["day"].dt.day) == [1]


def test_days_without_any_value_are_not_returned():
    daily = previous_runs_to_daily(make_response(empty_first_day=True), (1,))
    assert list(daily["day"].dt.day) == [2]


def test_one_block_of_rows_per_lead_time():
    daily = previous_runs_to_daily(make_response(lead_days=(1, 3)), (1, 3))
    assert sorted(set(daily["lead_days"])) == [1, 3] and len(daily) == 4


def test_fetch_asks_for_the_right_columns_and_dates():
    session = FakeSession(FakeResponse(200, make_response(lead_days=(1, 3))))
    fetch_previous_runs(41.56, 0.31, date(2026, 1, 1), date(2026, 1, 2), lead_days=(1, 3), session=session)
    _, params, _ = session.calls[0]
    assert params["start_date"] == "2026-01-01" and params["end_date"] == "2026-01-02"
    assert params["timezone"] == "UTC"
    assert "precipitation_previous_day1" in params["hourly"] and "et0_fao_evapotranspiration_previous_day3" in params["hourly"]


def test_by_default_it_asks_for_every_lead_time_from_0_to_6():
    session = FakeSession(FakeResponse(200, make_response(lead_days=tuple(range(0, 7)))))
    daily = fetch_previous_runs(41.56, 0.31, date(2026, 1, 1), date(2026, 1, 2), session=session)
    asked = session.calls[0][1]["hourly"].split(",")
    assert "precipitation" in asked and "et0_fao_evapotranspiration" in asked  # antelación 0: sin sufijo
    assert all(f"precipitation_previous_day{lead}" in asked for lead in range(1, 7))
    assert "precipitation_previous_day0" not in asked and "precipitation_previous_day7" not in asked
    assert sorted(set(daily["lead_days"])) == [0, 1, 2, 3, 4, 5, 6]


def test_a_long_range_is_split_in_requests_without_gaps_or_overlaps(monkeypatch):
    monkeypatch.setattr("irrigation_app.weather.open_meteo.MAX_DAYS_PER_REQUEST", 3)

    class ByDates(FakeSession):
        def get(self, url, params, timeout):
            self.calls.append((url, params, timeout))
            first = date.fromisoformat(params["start_date"])
            last = date.fromisoformat(params["end_date"])
            days = (last - first).days + 1
            response = make_response(days=days, lead_days=(0,))
            response["hourly"]["time"] = [
                f"{(first + timedelta(days=d)).isoformat()}T{h:02d}:00" for d in range(days) for h in range(24)
            ]
            return FakeResponse(200, response)

    session = ByDates()
    daily = fetch_previous_runs(41.56, 0.31, date(2026, 1, 1), date(2026, 1, 8), lead_days=(0,), session=session)
    windows = [(c[1]["start_date"], c[1]["end_date"]) for c in session.calls]
    assert windows == [("2026-01-01", "2026-01-03"), ("2026-01-04", "2026-01-06"), ("2026-01-07", "2026-01-08")]
    assert list(daily["day"].dt.day) == list(range(1, 9))


def test_rate_limit_has_its_own_error_with_the_reason():
    session = FakeSession(FakeResponse(429, {"reason": "Hourly API request limit exceeded"}))
    with pytest.raises(RateLimitedError, match="Hourly API request limit exceeded"):
        fetch_previous_runs(41.56, 0.31, date(2026, 1, 1), date(2026, 1, 2), session=session)


def test_errors_do_not_leak_the_coordinates(monkeypatch):
    monkeypatch.setattr("irrigation_app.weather.open_meteo.time.sleep", lambda seconds: None)
    session = FakeSession(error=requests.exceptions.ConnectionError("https://x?latitude=41.56&longitude=0.31"))
    with pytest.raises(WeatherError) as error:
        fetch_previous_runs(41.56, 0.31, date(2026, 1, 1), date(2026, 1, 2), session=session)
    assert "41.56" not in str(error.value)


def test_a_slow_answer_is_retried_and_then_succeeds(monkeypatch):
    monkeypatch.setattr("irrigation_app.weather.open_meteo.time.sleep", lambda seconds: None)
    good = FakeResponse(200, make_response())

    class SlowOnce(FakeSession):
        def get(self, url, params, timeout):
            self.calls.append((url, params, timeout))
            if len(self.calls) == 1:
                raise requests.exceptions.ReadTimeout("lento")
            return good

    session = SlowOnce()
    daily = fetch_previous_runs(41.56, 0.31, date(2026, 1, 1), date(2026, 1, 2), lead_days=(1,), session=session)
    assert len(session.calls) == 2 and len(daily) == 2


class Sequence(FakeSession):
    """Responde en orden con las respuestas dadas y se queda con la última."""

    def __init__(self, *responses):
        super().__init__()
        self.responses = list(responses)

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def test_a_server_error_is_retried(monkeypatch):
    monkeypatch.setattr("irrigation_app.weather.open_meteo.time.sleep", lambda seconds: None)
    session = Sequence(FakeResponse(502, {"reason": "Bad gateway"}), FakeResponse(200, make_response()))
    daily = fetch_previous_runs(41.56, 0.31, date(2026, 1, 1), date(2026, 1, 2), lead_days=(1,), session=session)
    assert len(session.calls) == 2 and len(daily) == 2


def test_too_many_concurrent_requests_is_retried(monkeypatch):
    monkeypatch.setattr("irrigation_app.weather.open_meteo.time.sleep", lambda seconds: None)
    session = Sequence(FakeResponse(429, {"reason": "Too many concurrent requests"}), FakeResponse(200, make_response()))
    fetch_previous_runs(41.56, 0.31, date(2026, 1, 1), date(2026, 1, 2), lead_days=(1,), session=session)
    assert len(session.calls) == 2


def test_a_usage_limit_is_not_retried(monkeypatch):
    monkeypatch.setattr("irrigation_app.weather.open_meteo.time.sleep", lambda seconds: None)
    session = Sequence(FakeResponse(429, {"reason": "Hourly API request limit exceeded"}))
    with pytest.raises(RateLimitedError):
        fetch_previous_runs(41.56, 0.31, date(2026, 1, 1), date(2026, 1, 2), session=session)
    assert len(session.calls) == 1


def test_it_gives_up_after_three_attempts(monkeypatch):
    monkeypatch.setattr("irrigation_app.weather.open_meteo.time.sleep", lambda seconds: None)
    session = FakeSession(error=requests.exceptions.ReadTimeout("lento"))
    with pytest.raises(WeatherError):
        fetch_previous_runs(41.56, 0.31, date(2026, 1, 1), date(2026, 1, 2), session=session)
    assert len(session.calls) == 3


def test_response_without_hourly_data_is_an_error():
    with pytest.raises(WeatherError):
        previous_runs_to_daily({"daily": {}}, (1,))
