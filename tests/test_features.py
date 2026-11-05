"""Pruebas de las variables meteorológicas, con datos sintéticos."""

import numpy as np
import pandas as pd
import pytest

from irrigation_app.features import (
    INCOMPLETE_COLUMN,
    WEATHER_FEATURE_COLUMNS,
    add_weather_features,
    forecast_days,
    weather_features,
)

MONDAY = "2026-03-02"  # lunes
UNIT_LOCATIONS = pd.DataFrame({"unit_id": [1, 2], "location_id": ["A", "A"]})


def make_weather(rows, location="A"):
    """rows: (día, antelación, lluvia, et0, temperatura, humedad, viento)."""
    frame = pd.DataFrame(
        rows,
        columns=["day", "lead_days", "precipitation_mm", "et0_mm", "temperature_c", "humidity_pct", "wind_kmh"],
    )
    frame["day"] = pd.to_datetime(frame["day"], utc=True)
    frame.insert(0, "location_id", location)
    return frame


def make_examples(horizon, unit_id=1, origin=MONDAY):
    return pd.DataFrame(
        {"unit_id": [unit_id], "origin_day": [pd.Timestamp(origin, tz="UTC")], "horizon_days": [horizon]}
    )


def full_week():
    # lunes (lead 0), martes (lead 1), miércoles (lead 2); lead y lluvia distintos para ver cuál se usa.
    rows = []
    for lead in range(3):
        for offset in range(3):
            day = pd.Timestamp(MONDAY) + pd.Timedelta(days=offset)
            # Solo la combinación día = origen+lead es la que debe usarse.
            rain = 10.0 * (offset + 1) if offset == lead else 999.0
            rows.append((day, lead, rain, 1.0 * (offset + 1), 10.0 + offset, 50.0 + offset, 5.0 + offset))
    return make_weather(rows)


def test_horizon_1_sees_only_the_origin_day_with_lead_0():
    days = forecast_days(make_examples(1))
    assert days[["day", "lead_days"]].values.tolist() == [[pd.Timestamp(MONDAY, tz="UTC"), 0]]


def test_horizon_3_sees_monday_tuesday_wednesday_with_leads_0_1_2():
    days = forecast_days(make_examples(3))
    assert days["lead_days"].tolist() == [0, 1, 2]
    assert days["day"].dt.strftime("%Y-%m-%d").tolist() == ["2026-03-02", "2026-03-03", "2026-03-04"]


def test_features_use_each_day_with_its_own_lead_time():
    result = weather_features(make_examples(3), full_week(), UNIT_LOCATIONS).iloc[0]
    assert result["precip_sum_mm"] == 10 + 20 + 30  # nunca los 999 de otras antelaciones
    assert result["et0_sum_mm"] == 1 + 2 + 3
    assert result["temp_mean_c"] == pytest.approx(11.0)
    assert result["humidity_mean_pct"] == pytest.approx(51.0)
    assert result["wind_mean_kmh"] == pytest.approx(6.0)
    assert not result[INCOMPLETE_COLUMN]


def test_horizon_1_ignores_later_days():
    result = weather_features(make_examples(1), full_week(), UNIT_LOCATIONS).iloc[0]
    assert result["precip_sum_mm"] == 10
    assert result["temp_mean_c"] == pytest.approx(10.0)


def test_missing_weather_day_stays_null_and_is_flagged_not_invented():
    weather = full_week()
    weather = weather[~((weather["lead_days"] == 1) & (weather["day"] == pd.Timestamp("2026-03-03", tz="UTC")))]
    result = weather_features(make_examples(3), weather, UNIT_LOCATIONS).iloc[0]
    assert result[WEATHER_FEATURE_COLUMNS].isna().all()
    assert result[INCOMPLETE_COLUMN]


def test_missing_value_in_one_variable_only_nulls_that_variable():
    weather = full_week()
    weather.loc[(weather["lead_days"] == 2) & (weather["day"] == pd.Timestamp("2026-03-04", tz="UTC")), "wind_kmh"] = np.nan
    result = weather_features(make_examples(3), weather, UNIT_LOCATIONS).iloc[0]
    assert np.isnan(result["wind_mean_kmh"])
    assert result["precip_sum_mm"] == 60
    assert result[INCOMPLETE_COLUMN]


def test_unit_without_location_is_incomplete():
    result = weather_features(make_examples(1, unit_id=99), full_week(), UNIT_LOCATIONS).iloc[0]
    assert result[WEATHER_FEATURE_COLUMNS].isna().all()
    assert result[INCOMPLETE_COLUMN]


def test_shared_location_gives_the_same_weather_to_both_units():
    examples = pd.concat([make_examples(3, unit_id=1), make_examples(3, unit_id=2)], ignore_index=True)
    result = weather_features(examples, full_week(), UNIT_LOCATIONS)
    assert len(result) == 2
    assert result["precip_sum_mm"].tolist() == [60, 60]


def test_different_horizons_of_the_same_origin_are_separate_examples():
    examples = pd.concat([make_examples(1), make_examples(2), make_examples(3)], ignore_index=True)
    result = weather_features(examples, full_week(), UNIT_LOCATIONS).sort_values("horizon_days")
    assert result["precip_sum_mm"].tolist() == [10, 30, 60]


def test_add_weather_features_keeps_rows_order_and_repeats_per_depth():
    examples = pd.DataFrame(
        {
            "unit_id": [1, 1, 1],
            "depth_cm": [10, 30, 10],
            "origin_day": pd.to_datetime([MONDAY, MONDAY, "2026-03-09"], utc=True),
            "horizon_days": [3, 3, 1],
        }
    )
    result = add_weather_features(examples, full_week(), UNIT_LOCATIONS)
    assert result["depth_cm"].tolist() == [10, 30, 10]
    assert result["precip_sum_mm"].iloc[:2].tolist() == [60, 60]
    assert result[INCOMPLETE_COLUMN].tolist() == [False, False, True]
    assert result["precip_sum_mm"].iloc[2] != result["precip_sum_mm"].iloc[2]  # nulo


def test_duplicate_weather_rows_are_rejected():
    weather = pd.concat([full_week(), full_week()], ignore_index=True)
    with pytest.raises(ValueError):
        weather_features(make_examples(1), weather, UNIT_LOCATIONS)
