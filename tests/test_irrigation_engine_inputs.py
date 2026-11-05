"""Entrada y salida del motor: un valor imposible se rechaza con un error que nombra el campo."""

import pytest
from pydantic import ValidationError

from irrigation_app.irrigation_engine import EngineInput, SensorPrediction, WeatherDay
from irrigation_app.irrigation_engine.result import DayPlan, EngineResult
from irrigation_app.reference_plot import REFERENCE_PLOT

from irrigation_engine_factory import ORIGIN, engine_input, predictions


def test_efficiency_of_1_4_is_rejected_naming_the_field():
    with pytest.raises(ValidationError, match="irrigation_efficiency"):
        engine_input({}, irrigation_efficiency=1.4)


def test_moisture_outside_the_index_is_rejected_naming_the_field():
    with pytest.raises(ValidationError, match="moisture_pred"):
        SensorPrediction(depth_cm=15, horizon_days=1, moisture_pred=105.0, quality="validated", method="model")


def test_horizon_outside_1_to_7_is_rejected():
    with pytest.raises(ValidationError, match="horizon_days"):
        SensorPrediction(depth_cm=15, horizon_days=8, moisture_pred=50.0, quality="validated", method="model")


def test_usable_prediction_needs_a_value_but_insufficient_data_does_not():
    with pytest.raises(ValidationError, match="moisture_pred"):
        SensorPrediction(depth_cm=15, horizon_days=1, quality="provisional", method="persistence_fallback")
    ok = SensorPrediction(depth_cm=15, horizon_days=1, quality="insufficient_data")
    assert ok.moisture_pred is None


def test_negative_rain_and_absurd_eto_are_rejected():
    with pytest.raises(ValidationError, match="rain_mm"):
        WeatherDay(date=ORIGIN, rain_mm=-1)
    with pytest.raises(ValidationError, match="eto_mm"):
        WeatherDay(date=ORIGIN, eto_mm=60)


def test_duplicates_are_rejected():
    dup = predictions({1: 70.0}) + predictions({1: 71.0})
    with pytest.raises(ValidationError, match="misma sonda"):
        EngineInput(plot=REFERENCE_PLOT, origin_date=ORIGIN, predictions=tuple(dup))
    with pytest.raises(ValidationError, match="misma fecha"):
        EngineInput(plot=REFERENCE_PLOT, origin_date=ORIGIN, weather=(WeatherDay(date=ORIGIN), WeatherDay(date=ORIGIN)))


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        WeatherDay(date=ORIGIN, temperature=20)


def test_prediction_limitations_keep_their_parameters():
    p = SensorPrediction(
        depth_cm=15, horizon_days=1, moisture_pred=60.0, quality="provisional", method="model",
        limitations=[{"code": "stale_reading", "age_hours": 50}],
    )
    assert p.limitations[0].code == "stale_reading" and p.limitations[0].model_extra == {"age_hours": 50}


def test_result_has_no_duration_field_because_there_is_no_flow_rate():
    fields = set(DayPlan.model_fields) | set(EngineResult.model_fields)
    assert not [f for f in fields if "duration" in f or "minutes" in f]
    assert "volume_m3" in fields and EngineResult.model_fields["indicative"].default is True
