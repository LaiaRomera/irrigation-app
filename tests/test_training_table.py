"""Pruebas de la tabla de entrenamiento, con datos sintéticos."""

import numpy as np
import pandas as pd
import pytest

from irrigation_app.training_table import (
    TRAINING_COLUMNS,
    build_training_table,
    build_training_table_with_counts,
    dawn_snapshots,
    seasonal_features,
)

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


# ---------- Exclusión por riego e instalación (2.3) ----------

def day(text):
    return pd.Timestamp(text, tz="UTC")


def irrigation_marks(*marked_days, unit_id=1):
    """Salida mínima de `possible_irrigation`: solo los días marcados."""
    return pd.DataFrame({"unit_id": unit_id, "day": [day(d) for d in marked_days], "possible_irrigation": True})


def installation(*days, unit_id=1):
    return pd.DataFrame({"unit_id": unit_id, "day": [day(d) for d in days]})


def horizons_kept(table, origin):
    return sorted(table[table["origin_day"] == day(origin)]["horizon_days"])


def test_an_irrigation_at_5_days_removes_horizons_5_to_7_and_keeps_1_to_4():
    snapshots = make_snapshots([50.0] * 10)
    table = build_training_table(snapshots, irrigation=irrigation_marks("2026-01-06"))  # origen 1 de enero
    assert horizons_kept(table, "2026-01-01") == [1, 2, 3, 4]


def test_an_irrigation_before_or_on_the_origin_day_does_not_exclude():
    snapshots = make_snapshots([50.0] * 10)
    table = build_training_table(snapshots, irrigation=irrigation_marks("2026-01-03", "2026-01-04"))
    assert horizons_kept(table, "2026-01-04") == [1, 2, 3, 4, 5, 6]  # riego de «ayer» y del propio origen
    assert horizons_kept(table, "2026-01-02") == []  # con origen el día 2, el riego del día 3 está a 1 día


def test_irrigation_exclusion_applies_to_every_depth_of_the_unit_but_not_to_other_units():
    a = make_snapshots([50.0] * 4)
    b = a.assign(depth_cm=20)
    c = a.assign(unit_id=2)
    table = build_training_table(pd.concat([a, b, c], ignore_index=True), irrigation=irrigation_marks("2026-01-02"))
    origin = table[table["origin_day"] == day("2026-01-01")]
    assert set(origin[origin["unit_id"] == 1]["horizon_days"]) == set()  # riego mañana: no queda ni h=1
    assert set(origin[origin["unit_id"] == 2]["horizon_days"]) == {1, 2, 3}


def test_the_irrigation_exclusion_uses_the_real_rule_with_unknown_rain_not_discarding():
    from irrigation_app.irrigation_marks import possible_irrigation

    snapshots = make_snapshots([50.0, 50.0, 60.0, 60.0, 60.0])  # sube 10 el día 3
    unknown_rain = pd.DataFrame({"unit_id": [1], "day": [day("2026-01-03")], "rain_mm": [float("nan")]})
    marks = possible_irrigation(snapshots, rain=unknown_rain)
    table = build_training_table(snapshots, horizons=(1, 2), irrigation=marks)
    assert horizons_kept(table, "2026-01-01") == [1]  # el riego del día 3 está a 2 días: cae el horizonte 2
    assert horizons_kept(table, "2026-01-02") == []  # el día 3 es origen+1
    assert horizons_kept(table, "2026-01-03") == [1, 2]  # el riego del propio origen no cuenta


def test_installation_days_are_removed_as_origin_and_as_target():
    snapshots = make_snapshots([5.0, 5.0, 70.0, 70.0, 70.0, 70.0])
    table = build_training_table(snapshots, horizons=(1, 2), installation=installation("2026-01-01", "2026-01-02"))
    assert set(zip(table["origin_day"].dt.day, table["horizon_days"])) == {(3, 1), (4, 1), (5, 1), (3, 2), (4, 2)}


def test_a_lag_against_an_installation_day_becomes_null_but_the_example_is_kept():
    snapshots = make_snapshots([5.0, 70.0, 71.0, 72.0, 73.0, 74.0, 75.0])
    table, counts = build_training_table_with_counts(
        snapshots, horizons=(1,), installation=installation("2026-01-01")
    )
    by_day = table.set_index(table["origin_day"].dt.day)
    assert pd.isna(by_day.loc[2, "change_1d"])  # su referencia (el día 1) es instalación
    assert by_day.loc[2, "target_change"] == pytest.approx(1.0)  # pero el objetivo es normal: se conserva
    assert by_day.loc[3, "change_1d"] == pytest.approx(1.0)
    assert pd.isna(by_day.loc[4, "change_3d"]) and by_day.loc[4, "change_1d"] == pytest.approx(1.0)
    assert by_day.loc[5, "change_3d"] == pytest.approx(73 - 70)
    assert counts.loc[1, "change_1d_nulled"] == 1  # solo el día 2
    assert counts.loc[1, "change_3d_nulled"] == 1  # solo el día 4


def test_a_lag_that_was_already_missing_is_not_counted_as_nulled_by_installation():
    snapshots = make_snapshots([70.0, 70.0, 70.0])
    _, counts = build_training_table_with_counts(snapshots, horizons=(1,), installation=installation("2025-12-31"))
    assert counts.loc[1, "change_1d_nulled"] == 0


def test_counts_add_up_per_horizon():
    snapshots = make_snapshots([5.0, 70.0, 70.0, 70.0, 70.0, 70.0, 70.0, 70.0])
    table, counts = build_training_table_with_counts(
        snapshots,
        horizons=(1, 3),
        installation=installation("2026-01-01"),
        irrigation=irrigation_marks("2026-01-05"),
    )
    assert (counts["initial"] - counts["removed_installation"] - counts["removed_irrigation"] == counts["final"]).all()
    assert counts["final"].to_dict() == table.groupby("horizon_days").size().to_dict()
    assert counts.loc[1, ["initial", "removed_installation", "removed_irrigation", "final"]].tolist() == [7, 1, 1, 5]
    assert counts.loc[3, ["initial", "removed_installation", "removed_irrigation", "final"]].tolist() == [5, 1, 3, 1]


def test_without_installation_or_irrigation_nothing_is_excluded():
    table = build_training_table(make_snapshots([70, 68, 65, 60]), horizons=(1,))
    assert len(table) == 3


# ---------- Variables de estación (2.4) ----------

def test_season_is_continuous_between_31_december_and_1_january():
    days = pd.Series(pd.to_datetime(["2026-12-30", "2026-12-31", "2027-01-01", "2027-01-02"], utc=True))
    season = seasonal_features(days)
    normal_step = 2 * np.pi / 365.25
    steps = np.hypot(season["doy_sin"].diff(), season["doy_cos"].diff()).dropna().to_numpy()
    assert ((steps > 0.9 * normal_step) & (steps < 1.3 * normal_step)).all()  # sin salto al cambiar de año
    assert np.hypot(season["doy_sin"], season["doy_cos"]).to_numpy() == pytest.approx(1.0)


def test_season_columns_follow_the_origin_day_and_the_old_columns_are_kept():
    snapshots = make_snapshots([70, 68, 65], start="2026-12-31")
    table = build_training_table(snapshots, horizons=(1,))
    first = table.iloc[0]
    assert first["doy_sin"] == pytest.approx(np.sin(2 * np.pi * 365 / 365.25))
    for column in ("depth_cm", "base_index", "change_1d", "change_3d", "doy_sin", "doy_cos"):
        assert column in table.columns
