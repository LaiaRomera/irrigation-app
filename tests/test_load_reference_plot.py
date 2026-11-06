"""scripts/load_reference_plot.py con datos sintéticos; la carga a PostgreSQL, en el Docker local."""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from irrigation_app import plot_code
from irrigation_app.reference_plot import REFERENCE_PLOT

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "load_reference_plot.py"
_spec = importlib.util.spec_from_file_location("load_reference_plot", _PATH)
lrp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lrp)

UNIT = pd.Series({"unit_id": 1168, "smm_min": 6900, "smm_max": 11700, "latitude": 41.9884, "longitude": 0.2085})


def raw_readings(depths=(15, 25, 35, 55), n=4):
    stamps = pd.date_range("2026-08-01", periods=n, freq="15min", tz="UTC")
    rows = []
    for depth in depths:
        for i, ts in enumerate(stamps):
            kind = ["SMM", "SMMi", "SMMAdmin", "SMM"][i % 4]
            rows.append((1168, ts, depth, 9300, kind))
    rows.append((9999, stamps[0], 15, 9300, "SMM"))  # otra unidad: no debe entrar
    return pd.DataFrame(rows, columns=["unit_id", "timestamp", "depth_cm", "frequency", "reading_type"])


def test_rows_use_the_index_scale_and_only_the_units_readings():
    rows = lrp.build_reading_rows(raw_readings(), UNIT, REFERENCE_PLOT.sensor_depths_cm)
    assert len(rows) == 16
    assert rows["value"].iloc[0] == pytest.approx((11700 - 9300) / 4800 * 100)  # 50
    assert set(rows["depth_cm"]) == {15, 25, 35, 55}


def test_interpolated_and_admin_flags():
    rows = lrp.build_reading_rows(raw_readings(depths=(15,)), UNIT, REFERENCE_PLOT.sensor_depths_cm)
    assert rows["is_interpolated"].tolist() == [False, True, False, False]
    assert rows["quality_flag"].tolist() == ["ok", "ok", "admin", "ok"]
    assert rows.loc[2, "quality_reason"] and pd.isna(rows.loc[0, "quality_reason"])


def test_a_depth_without_sensor_is_an_error():
    with pytest.raises(ValueError, match="sin sensor"):
        lrp.build_reading_rows(raw_readings(depths=(15, 99)), UNIT, REFERENCE_PLOT.sensor_depths_cm)


@pytest.mark.integration
def test_loading_twice_does_not_duplicate_anything(pg_connection):
    rows = lrp.build_reading_rows(raw_readings(), UNIT, REFERENCE_PLOT.sensor_depths_cm)
    first = lrp.load_reference_plot(pg_connection, REFERENCE_PLOT, UNIT, rows)
    second = lrp.load_reference_plot(pg_connection, REFERENCE_PLOT, UNIT, rows)
    assert first["readings_inserted"] == 16 and second["readings_inserted"] == 0
    assert first["plot_id"] == second["plot_id"] and first["user_id"] == second["user_id"]
    assert first["plot_code"] == second["plot_code"]  # el código se genera una vez y no cambia
    assert pg_connection.execute("SELECT plot_code FROM plot").fetchone()[0] == first["plot_code"]
    assert plot_code.is_valid_plot_code(first["plot_code"])
    counts = [pg_connection.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("app_user", "plot", "sensor", "sensor_reading")]
    assert counts == [1, 1, 4, 16]
    plot = pg_connection.execute("SELECT name, crop, soil_texture, timezone FROM plot").fetchone()
    assert plot == ("1168_P2", "manzano", "franco arcilloso limoso", "Europe/Madrid")
    codes = {r[0] for r in pg_connection.execute("SELECT external_code FROM sensor")}
    assert codes == {"1168_15", "1168_25", "1168_35", "1168_55"}
    flags = dict(pg_connection.execute("SELECT quality_flag, count(*) FROM sensor_reading GROUP BY 1").fetchall())
    assert flags == {"ok": 12, "admin": 4}
