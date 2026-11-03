"""Pruebas de la escala común de humedad, con datos sintéticos."""

import pandas as pd
import pytest

from irrigation_app.scale import add_relative_moisture

UNITS = pd.DataFrame(
    {"unit_id": [1, 2, 3], "smm_min": [6900, 6900, 6900], "smm_max": [11700, 9000, 6900]}
)


def make_daily(unit_id, mean, low, high):
    return pd.DataFrame(
        {"unit_id": [unit_id], "frequency_mean": [mean], "frequency_min": [low], "frequency_max": [high]}
    )


def test_extremes_of_the_range_map_to_0_and_100():
    daily = pd.concat([make_daily(1, 11700, 11700, 11700), make_daily(1, 6900, 6900, 6900)], ignore_index=True)
    result = add_relative_moisture(daily, UNITS)
    assert list(result["moisture_index_mean"]) == [0, 100]


def test_each_unit_uses_its_own_range():
    daily = pd.concat([make_daily(1, 8000, 8000, 8000), make_daily(2, 8000, 8000, 8000)], ignore_index=True)
    result = add_relative_moisture(daily, UNITS)
    assert result["moisture_index_mean"].iloc[0] == pytest.approx((11700 - 8000) / 4800 * 100)
    assert result["moisture_index_mean"].iloc[1] == pytest.approx((9000 - 8000) / 2100 * 100)


def test_daily_max_frequency_gives_min_index_and_vice_versa():
    result = add_relative_moisture(make_daily(1, 9000, 8000, 10000), UNITS).iloc[0]
    assert result["moisture_index_min"] < result["moisture_index_mean"] < result["moisture_index_max"]


def test_values_outside_the_range_are_not_clipped():
    result = add_relative_moisture(make_daily(1, 6500, 6500, 6500), UNITS)
    assert result["moisture_index_mean"].iloc[0] > 100


def test_unit_without_valid_range_gets_nulls():
    result = add_relative_moisture(make_daily(3, 8000, 8000, 8000), UNITS)
    assert result["moisture_index_mean"].isna().all()
