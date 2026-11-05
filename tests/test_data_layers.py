"""Las rutas de las capas de datos están bien formadas y los scripts no usan las rutas viejas."""

import re
from pathlib import Path

from irrigation_app import data_layers

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Se construyen por partes para que esta prueba no se encuentre a sí misma.
OLD_PATH_TEXT = "data" + "/agrodevices"
OLD_PATH_JOINED = re.compile(r'"data"\s*/\s*"agrodevices"')
# La meteorología vivía en una carpeta propia; ahora está repartida en bronce y plata.
OLD_WEATHER_TEXT = "data" + "/weather"
OLD_WEATHER_JOINED = re.compile(r'"data"\s*/\s*"weather"')


def test_layer_directories_hang_from_project_data():
    assert data_layers.PROJECT_ROOT == PROJECT_ROOT
    assert data_layers.DATA_DIR == PROJECT_ROOT / "data"
    assert data_layers.BRONZE_DIR == data_layers.DATA_DIR / "bronze"
    assert data_layers.SILVER_DIR == data_layers.DATA_DIR / "silver"
    assert data_layers.GOLD_DIR == data_layers.DATA_DIR / "gold"


def test_files_live_in_their_layer():
    for path in data_layers.SILVER_FILES:
        assert path.parent == data_layers.SILVER_DIR
    for path in data_layers.GOLD_FILES:
        assert path.parent == data_layers.GOLD_DIR
    assert data_layers.TRAINING_TABLE_PATH in data_layers.GOLD_FILES
    assert {p.name for p in data_layers.SILVER_FILES} == {
        "units.parquet", "readings.parquet", "daily.parquet", "snapshots.parquet", "muestra_unidad_1130.csv",
        "weather_previous_runs.parquet", "weather_locations.parquet", "weather_unit_locations.parquet",
    }


def test_weather_is_split_between_bronze_and_silver():
    assert data_layers.BRONZE_WEATHER_DIR == data_layers.BRONZE_DIR / "weather" / "previous_runs"
    for path in (
        data_layers.WEATHER_PREVIOUS_RUNS_PATH,
        data_layers.WEATHER_LOCATIONS_PATH,
        data_layers.WEATHER_UNIT_LOCATIONS_PATH,
    ):
        assert path.parent == data_layers.SILVER_DIR
        assert path in data_layers.SILVER_FILES
    assert data_layers.TRAINING_PARAMS_PATH.parent == data_layers.GOLD_DIR


def test_bronze_exports_dir_comes_from_environment(monkeypatch):
    monkeypatch.delenv(data_layers.BRONZE_EXPORTS_ENV, raising=False)
    assert data_layers.bronze_exports_dir() is None
    monkeypatch.setenv(data_layers.BRONZE_EXPORTS_ENV, "exports")
    assert data_layers.bronze_exports_dir() == Path("exports")


def test_scripts_and_tests_do_not_use_old_paths():
    this_file = Path(__file__).resolve()
    files = [
        p
        for folder in ("scripts", "tests")
        for p in (PROJECT_ROOT / folder).glob("*.py")
        if p.resolve() != this_file
    ]
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert OLD_PATH_TEXT not in text, path.name
        assert not OLD_PATH_JOINED.search(text), path.name
        assert OLD_WEATHER_TEXT not in text, path.name
        assert not OLD_WEATHER_JOINED.search(text), path.name


def test_download_weather_uses_layer_constants():
    text = (PROJECT_ROOT / "scripts" / "download_weather.py").read_text(encoding="utf-8")
    assert "irrigation_app.data_layers" in text
    assert "BRONZE_WEATHER_DIR" in text and "WEATHER_PREVIOUS_RUNS_PATH" in text


def test_prepare_scripts_use_layer_constants():
    for path in (PROJECT_ROOT / "scripts").glob("prepare_*.py"):
        assert "irrigation_app.data_layers" in path.read_text(encoding="utf-8"), path.name
