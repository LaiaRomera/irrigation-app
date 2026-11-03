"""Pruebas de la tabla de lecturas de Agrodevices, con datos sintéticos."""

import pandas as pd
import pytest

from irrigation_app.sources.agrodevices import DEPTH_COLUMNS, build_units, read_devices, read_profiles, read_readings

DEVICES_CSV = """"Device_ID","Model","UserName","ProjectID","ClientID","Latitud","Longitud","Activo"
10,SMM,Finca A,1,100,41.5,0.3,1
"""

PROFILES_CSV = """"id","UnitID","Levels","AverageFreqLevels","SensorTemp","SMMMin","SMMMax","PasarelaID","SensorPressure"
1,10,"015-025-000-045-000-000-000-000","1,1,1,1,0",0,6900,11700,0,0
"""

# Unidad 10 con canales 1, 2 y 4 configurados; la 99 no está en la tabla de unidades.
READINGS_CSV = """UnitID,fecha,Type,Frec_1,Frec_2,Frec_3,Frec_4,Frec_5,Otra
10,2026-01-01 00:00:00,SMM,8000,9000,7000,10000,0,1
10,2026-01-01 00:15:00,SMMi,8010,0,7010,10010,0,1
10,2026-01-01 00:30:00,SMM0,0,0,0,0,0,1
10,2026-01-01 00:45:00,SMMAdmin,8030,9030,0,10030,0,1
99,2026-01-01 00:00:00,SMM,8000,9000,7000,10000,0,1
"""


@pytest.fixture
def units_and_readings(tmp_path):
    for name, content in (("devices", DEVICES_CSV), ("profiles", PROFILES_CSV), ("readings", READINGS_CSV)):
        (tmp_path / f"{name}.csv").write_text(content, encoding="utf-8")
    units, _ = build_units(read_devices(tmp_path / "devices.csv"), read_profiles(tmp_path / "profiles.csv"))
    return units, tmp_path / "readings.csv"


def test_one_row_per_instant_and_depth_with_zero_channels_dropped(units_and_readings):
    units, path = units_and_readings
    readings, _ = read_readings(path, units, chunk_rows=2)
    # Canal 3 no tiene profundidad; canal 5 vale 0; el 0 del canal 2 a las 00:15 es dato ausente.
    first = readings[readings["timestamp"] == pd.Timestamp("2026-01-01 00:00", tz="UTC")]
    assert dict(zip(first["depth_cm"], first["frequency"])) == {15: 8000, 25: 9000, 45: 10000}
    second = readings[readings["timestamp"] == pd.Timestamp("2026-01-01 00:15", tz="UTC")]
    assert dict(zip(second["depth_cm"], second["frequency"])) == {15: 8010, 45: 10010}


def test_smm0_rows_are_excluded_and_other_types_kept_flagged(units_and_readings):
    units, path = units_and_readings
    readings, report = read_readings(path, units)
    assert set(readings["reading_type"]) == {"SMM", "SMMi", "SMMAdmin"}
    assert report.rows_excluded_by_type == {"SMM0": 1}


def test_units_missing_from_the_table_are_ignored_and_counted(units_and_readings):
    units, path = units_and_readings
    readings, report = read_readings(path, units)
    assert set(readings["unit_id"]) == {10}
    assert report.rows_read == 5 and report.rows_other_units == 1 and report.rows_kept == 3


def test_timestamps_are_utc_and_output_is_sorted(units_and_readings):
    units, path = units_and_readings
    readings, _ = read_readings(path, units, chunk_rows=2)
    assert str(readings["timestamp"].dt.tz) == "UTC"
    assert readings.equals(readings.sort_values(["unit_id", "timestamp", "depth_cm"]).reset_index(drop=True))


def test_value_counts_add_up(units_and_readings):
    units, path = units_and_readings
    readings, report = read_readings(path, units)
    assert report.values_kept == len(readings)
    assert report.values_kept + report.values_discarded == report.rows_kept * 5
    assert not set(DEPTH_COLUMNS) & set(readings.columns)
