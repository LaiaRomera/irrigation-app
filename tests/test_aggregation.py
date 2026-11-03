"""Pruebas de la agregación diaria, con datos sintéticos."""

import pandas as pd

from irrigation_app.aggregation import DAILY_COLUMNS, aggregate_daily


def make_readings(rows):
    frame = pd.DataFrame(rows, columns=["unit_id", "timestamp", "depth_cm", "frequency", "reading_type"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame


def test_statistics_and_counts_per_type_within_a_day():
    readings = make_readings(
        [
            (1, "2026-01-01 00:00", 10, 8000, "SMM"),
            (1, "2026-01-01 12:00", 10, 8200, "SMMi"),
            (1, "2026-01-01 23:45", 10, 8100, "SMMAdmin"),
        ]
    )
    daily = aggregate_daily(readings)
    assert list(daily.columns) == DAILY_COLUMNS
    row = daily.iloc[0]
    assert (row["frequency_mean"], row["frequency_min"], row["frequency_max"]) == (8100, 8000, 8200)
    assert (row["n_readings"], row["n_real"], row["n_interpolated"], row["n_admin"]) == (3, 1, 1, 1)


def test_one_row_per_unit_day_and_depth():
    readings = make_readings(
        [
            (1, "2026-01-01 10:00", 10, 8000, "SMM"),
            (1, "2026-01-01 10:00", 20, 9000, "SMM"),
            (1, "2026-01-02 10:00", 10, 8100, "SMM"),
            (2, "2026-01-01 10:00", 10, 7000, "SMM"),
        ]
    )
    daily = aggregate_daily(readings)
    assert len(daily) == 4
    assert not daily.duplicated(["unit_id", "day", "depth_cm"]).any()


def test_day_boundary_is_utc_midnight():
    readings = make_readings(
        [
            (1, "2026-01-01 23:45", 10, 8000, "SMM"),
            (1, "2026-01-02 00:00", 10, 8100, "SMM"),
        ]
    )
    daily = aggregate_daily(readings)
    assert list(daily["day"].dt.strftime("%Y-%m-%d")) == ["2026-01-01", "2026-01-02"]
    assert str(daily["day"].dt.tz) == "UTC"
