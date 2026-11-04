"""Pruebas de la tabla de entrenamiento, con datos sintéticos."""

import pandas as pd
import pytest

from irrigation_app.training_table import TRAINING_COLUMNS, build_training_table, dawn_snapshots

UNITS = pd.DataFrame({"unit_id": [1], "smm_min": [6900], "smm_max": [11700]})


def make_readings(rows):
    frame = pd.DataFrame(rows, columns=["unit_id", "timestamp", "depth_cm", "frequency"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame


def make_snapshots(values, start="2026-01-01"):
    """Un índice de humedad por día consecutivo para la unidad 1, profundidad 10 (None = día sin dato)."""
    days = pd.date_range(start, periods=len(values), freq="D", tz="UTC")
    frame = pd.DataFrame({"unit_id": 1, "day": days, "depth_cm": 10, "moisture_index": values, "n_readings": 3})
    return frame[frame["moisture_index"].notna()].reset_index(drop=True)


def test_snapshot_is_the_mean_of_the_readings_around_the_origin_hour():
    readings = make_readings(
        [
            (1, "2026-01-01 04:45", 10, 9000),
            (1, "2026-01-01 05:00", 10, 9300),
            (1, "2026-01-01 05:15", 10, 9600),
            (1, "2026-01-01 12:00", 10, 7000),
            (1, "2026-01-01 05:30", 10, 7000),
        ]
    )
    snapshots = dawn_snapshots(readings, UNITS)
    assert len(snapshots) == 1
    assert snapshots["n_readings"].iloc[0] == 3
    assert snapshots["moisture_index"].iloc[0] == pytest.approx((11700 - 9300) / 4800 * 100)


def test_a_day_without_readings_near_the_origin_hour_has_no_snapshot():
    readings = make_readings([(1, "2026-01-01 12:00", 10, 9000), (1, "2026-01-02 05:00", 10, 9000)])
    snapshots = dawn_snapshots(readings, UNITS)
    assert list(snapshots["day"].dt.day) == [2]


def test_target_change_is_the_value_at_the_horizon_minus_the_origin():
    table = build_training_table(make_snapshots([70, 68, 65, 60]), horizons=(1, 3))
    row = table[(table["origin_day"].dt.day == 1) & (table["horizon_days"] == 3)].iloc[0]
    assert (row["base_index"], row["target_index"], row["target_change"]) == (70, 60, -10)
    assert list(TRAINING_COLUMNS) == list(table.columns)


def test_there_is_no_row_when_the_target_day_is_missing():
    table = build_training_table(make_snapshots([70, None, 65]), horizons=(1, 2))
    assert set(zip(table["origin_day"].dt.day, table["horizon_days"])) == {(1, 2)}


def test_lag_changes_describe_how_the_soil_was_moving():
    table = build_training_table(make_snapshots([80, 75, 72, 70, 69]), horizons=(1,))
    row = table[table["origin_day"].dt.day == 4].iloc[0]
    assert row["change_1d"] == pytest.approx(70 - 72)
    assert row["change_3d"] == pytest.approx(70 - 80)
    first = table[table["origin_day"].dt.day == 1].iloc[0]
    assert pd.isna(first["change_1d"]) and pd.isna(first["change_3d"])


def test_different_units_and_depths_do_not_mix():
    a = make_snapshots([70, 60])
    b = make_snapshots([50, 55]).assign(unit_id=2, depth_cm=20)
    table = build_training_table(pd.concat([a, b], ignore_index=True), horizons=(1,))
    changes = table.set_index("unit_id")["target_change"].to_dict()
    assert changes == {1: -10, 2: 5}
