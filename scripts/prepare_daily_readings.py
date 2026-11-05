"""Agrega las lecturas a un valor diario por unidad y profundidad y lo guarda en Parquet.

Uso (desde la raíz del proyecto, después de prepare_agrodevices_readings.py):
    .venv/Scripts/python scripts/prepare_daily_readings.py

Lee data/silver/readings.parquet y units.parquet, añade el índice de humedad relativa
(0 a 100 según el rango de cada sonda) y escribe data/silver/daily.parquet
(data/ no se sube a git).
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from irrigation_app.aggregation import aggregate_daily  # noqa: E402
from irrigation_app.data_layers import DAILY_PATH, READINGS_PATH, UNITS_PATH  # noqa: E402
from irrigation_app.scale import add_relative_moisture  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--readings", type=Path, default=READINGS_PATH)
    parser.add_argument("--units", type=Path, default=UNITS_PATH)
    parser.add_argument("--output", type=Path, default=DAILY_PATH)
    args = parser.parse_args()

    for path in (args.readings, args.units):
        if not path.exists():
            sys.exit(f"No existe el archivo {path}")

    readings = pd.read_parquet(args.readings)
    daily = add_relative_moisture(aggregate_daily(readings), pd.read_parquet(args.units))

    print(f"{len(readings):,} lecturas -> {len(daily):,} valores diarios")
    print(f"Unidades: {daily['unit_id'].nunique()}, días: {daily['day'].min().date()} a {daily['day'].max().date()}")
    print("Lecturas por valor diario (0-96 en una profundidad):")
    print(daily["n_readings"].describe().round(1).to_string())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    daily.to_parquet(args.output, index=False)
    print(f"\nGuardado en {args.output}")


if __name__ == "__main__":
    main()
