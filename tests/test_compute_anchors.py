"""Funciones de scripts/compute_anchors.py con series sintéticas."""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "compute_anchors.py"
_spec = importlib.util.spec_from_file_location("compute_anchors", _PATH)
ca = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ca)


def test_agrodevices_lines_convert_to_the_index():
    lines = ca.agrodevices_anchors(ca.AGRODEVICES_LINES_1168, 6900, 11700)
    assert lines["saturation"] == pytest.approx(79.396, abs=1e-3)
    assert lines["permanent_wilting_point"] == pytest.approx(59.708, abs=1e-3)
    assert lines["upper_optimal_recharge"] == pytest.approx(72.208, abs=1e-3)


def test_conversion_ends_of_range():
    assert ca.frequency_to_index(6900, 6900, 11700) == 100
    assert ca.frequency_to_index(11700, 6900, 11700) == 0
    with pytest.raises(ValueError):
        ca.frequency_to_index(8000, 9000, 9000)


def _snapshots():
    days = pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC")
    rows = []
    for d, v in zip(days, range(10, 110, 10)):
        for depth, offset in ((15, 0), (25, 2), (35, -2), (55, 40)):
            rows.append((1, d, depth, v + offset))
    return pd.DataFrame(rows, columns=["unit_id", "day", "depth_cm", "moisture_index"])


def _no_installation():
    return pd.DataFrame({"unit_id": pd.Series(dtype="int64"), "day": pd.Series(dtype="datetime64[ns, UTC]")})


def test_root_zone_mean_ignores_depths_outside_and_averages_the_rest():
    mean = ca.root_zone_mean(_snapshots(), 1, [15, 25, 35], _no_installation())
    assert list(mean) == pytest.approx(list(range(10, 110, 10)))  # +2 y -2 se compensan; el de 55 no entra


def test_root_zone_mean_excludes_installation_days_and_incomplete_days():
    snap = _snapshots()
    snap = snap[~((snap["day"] == snap["day"].min()) & (snap["depth_cm"] == 35))]  # falta una capa el día 1
    installation = pd.DataFrame({"unit_id": [1], "day": [pd.Timestamp("2026-01-02", tz="UTC")]})
    mean = ca.root_zone_mean(snap, 1, [15, 25, 35], installation)
    assert len(mean) == 8
    assert pd.Timestamp("2026-01-01", tz="UTC") not in mean.index
    assert pd.Timestamp("2026-01-02", tz="UTC") not in mean.index


def test_percentiles_and_share_below():
    series = pd.Series(range(0, 101), dtype=float)
    pct = ca.percentiles_of(series, (5, 50))
    assert pct == {5: 5.0, 50: 50.0}
    assert ca.share_below(series, 10) == pytest.approx(10 / 101)
    with pytest.raises(ValueError):
        ca.percentiles_of(pd.Series([], dtype=float))
