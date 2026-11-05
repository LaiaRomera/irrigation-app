"""Rutas de las capas de datos (arquitectura medallón) del proyecto.

- Bronce: los exports originales de Agrodevices. Son confidenciales y viven fuera del repositorio
  (no se copian a `data/`); solo se documentan con un manifiesto en `data/bronze/manifest.json`.
- Plata: datos limpios y tipados (`units`, `readings`, `daily`, `snapshots`).
- Oro: lo que consumen el modelo y la app (`training_table`).

Todos los scripts usan estas constantes en lugar de escribir las rutas a mano. `data/` no se sube a git.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"

BRONZE_DIR = DATA_DIR / "bronze"  # solo el manifiesto; los exports originales están fuera del repositorio
SILVER_DIR = DATA_DIR / "silver"
GOLD_DIR = DATA_DIR / "gold"

# Carpeta con los exports originales de Agrodevices (fuera del repositorio). Se indica con esta
# variable de entorno o con el argumento --bronze-dir de describe_data_layers.py.
BRONZE_EXPORTS_ENV = "AGRODEVICES_EXPORTS_DIR"

BRONZE_MANIFEST_PATH = BRONZE_DIR / "manifest.json"
SILVER_MANIFEST_PATH = SILVER_DIR / "manifest.json"
GOLD_MANIFEST_PATH = GOLD_DIR / "manifest.json"

UNITS_PATH = SILVER_DIR / "units.parquet"
READINGS_PATH = SILVER_DIR / "readings.parquet"
DAILY_PATH = SILVER_DIR / "daily.parquet"
SNAPSHOTS_PATH = SILVER_DIR / "snapshots.parquet"
SAMPLE_UNIT_1130_PATH = SILVER_DIR / "muestra_unidad_1130.csv"

TRAINING_TABLE_PATH = GOLD_DIR / "training_table.parquet"

SILVER_FILES = (UNITS_PATH, READINGS_PATH, DAILY_PATH, SNAPSHOTS_PATH, SAMPLE_UNIT_1130_PATH)
GOLD_FILES = (TRAINING_TABLE_PATH,)


def bronze_exports_dir() -> Path | None:
    """Carpeta de los exports originales según la variable de entorno, o None si no está definida."""
    value = os.environ.get(BRONZE_EXPORTS_ENV)
    return Path(value) if value else None
