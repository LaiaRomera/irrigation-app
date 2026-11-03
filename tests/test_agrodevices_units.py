"""Pruebas de la tabla de unidades de Agrodevices, con datos sintéticos."""

import pandas as pd
import pytest

from irrigation_app.sources.agrodevices import (
    build_units,
    read_devices,
    read_profiles,
    read_reading_unit_ids,
)

DEVICES_CSV = """"Device_ID","Model","UserName","ProjectID","ClientID","Latitud","Longitud","Activo"
0,PLAYER,"",0,1001,0.0,0.0,0
10,SMM,Finca A,1,100,41.5,0.3,1
11,SMM,Finca B,1,100,41.6,0.4,1
12,PLAYER,Otro modelo,1,100,41.7,0.5,1
13,SMM,Inactiva,1,100,41.8,0.6,0
2000,SMM,Demo,1,100,41.9,0.7,1
14,SMM,Sin lecturas,1,100,42.0,0.8,1
15,SMM,Sin perfil,1,100,42.1,0.9,1
16,SMM,Sin profundidad,1,100,42.2,1.0,1
17,SMM,Sin coordenadas,1,100,0.0,0.0,1
18,SMM,Project 1,1,100,44.5190842342537500,40.1956274998365100,1
19,SMM,Empresa 1,1,100,41.656,0.5568,1
20,SMM,Empresa 2,2,200,41.6561,0.5569,1
"""

PROFILES_CSV = """"id","UnitID","Levels","AverageFreqLevels","SensorTemp","SMMMin","SMMMax","PasarelaID","SensorPressure"
1,10,"015-025-035-000-055-000-000-000","1,1,1,1,0",0,6900,11700,0,0
2,11,"010-020-000-000-000-000-000-000","1,1,1,1,1",0,6900,9000,0,0
3,11,"010-020-030-000-050-000-000-000","1,1,1,1,1",0,6900,9000,0,0
4,14,"015-025-035-000-055-000-000-000","1,1,1,1,1",0,6900,11700,0,0
5,16,"000-000-000-000-000-040-050-060","1,1,1,1,1",0,6900,11700,0,0
6,17,"015-025-035-000-055-000-000-000","1,1,1,1,1",0,6900,11700,0,0
7,18,"015-025-035-000-055-000-000-000","1,1,1,1,1",0,6900,11700,0,0
8,19,"015-025-035-000-055-000-000-000","1,1,1,1,1",0,6900,11700,0,0
9,20,"015-025-035-000-055-000-000-000","1,1,1,1,1",0,6900,11700,0,0
10,2000,"015-025-035-000-055-000-000-000","1,1,1,1,1",0,6900,11700,0,0
11,12,"015-025-035-000-055-000-000-000","1,1,1,1,1",0,6900,11700,0,0
12,13,"015-025-035-000-055-000-000-000","1,1,1,1,1",0,6900,11700,0,0
"""

READINGS_CSV = "UnitID,Type\n" + "\n".join(f"{u},SMM" for u in (10, 11, 15, 16, 17, 18, 19, 20, 2000, 13, 12))


@pytest.fixture
def files(tmp_path):
    paths = {}
    for name, content in (("devices", DEVICES_CSV), ("profiles", PROFILES_CSV), ("readings", READINGS_CSV)):
        paths[name] = tmp_path / f"{name}.csv"
        paths[name].write_text(content, encoding="utf-8")
    return paths


def test_read_devices_renames_id_and_drops_unregistered_rows(files):
    devices = read_devices(files["devices"])
    assert "unit_id" in devices.columns and "Device_ID" not in devices.columns
    assert 0 not in set(devices["unit_id"])


def test_read_profiles_parses_depths_and_flags(files):
    profiles = read_profiles(files["profiles"]).set_index("unit_id")
    row = profiles.loc[10]
    assert [row[f"depth_cm_{i}"] for i in (1, 2, 3, 5)] == [15, 25, 35, 55]
    assert pd.isna(row["depth_cm_4"])
    assert [bool(row[f"in_average_{i}"]) for i in range(1, 6)] == [True, True, True, True, False]


def test_read_profiles_keeps_latest_row_of_repeated_unit(files):
    profiles = read_profiles(files["profiles"]).set_index("unit_id")
    assert profiles.loc[11, "depth_cm_3"] == 30
    assert len(profiles) == profiles.index.nunique()


def test_levels_beyond_the_fifth_are_ignored(files):
    profiles = read_profiles(files["profiles"]).set_index("unit_id")
    assert profiles.loc[16, [f"depth_cm_{i}" for i in range(1, 6)]].isna().all()


def test_build_units_applies_every_filter(files):
    reading_ids = read_reading_unit_ids(files["readings"])
    units, steps = build_units(read_devices(files["devices"]), read_profiles(files["profiles"]), reading_ids)

    removed = {step.name: set(step.removed_ids) for step in steps}
    assert removed["modelo SMM"] == {12}
    assert removed["activa (Activo = 1)"] == {13}
    assert removed["no es una unidad de demostración"] == {2000}
    assert removed["tiene lecturas"] == {14}
    assert removed["tiene configuración de sonda"] == {15}
    assert removed["tiene alguna profundidad"] == {16}
    assert removed["tiene coordenadas reales"] == {17, 18}
    assert list(units["unit_id"]) == [10, 11, 19, 20]


def test_units_sharing_a_place_across_clients_are_marked_shared(files):
    reading_ids = read_reading_unit_ids(files["readings"])
    units, _ = build_units(read_devices(files["devices"]), read_profiles(files["profiles"]), reading_ids)
    status = units.set_index("unit_id")["coord_status"]
    assert status[10] == "ok" and status[11] == "ok"
    assert status[19] == "shared" and status[20] == "shared"


def test_without_readings_file_the_readings_filter_is_skipped(files):
    _, steps = build_units(read_devices(files["devices"]), read_profiles(files["profiles"]))
    assert "tiene lecturas" not in [step.name for step in steps]
