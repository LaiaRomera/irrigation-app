"""Traduce el CSV de lecturas de Agrodevices a formato largo y lo guarda en Parquet.

Uso (desde la raíz del proyecto, después de prepare_agrodevices_units.py):
    .venv/Scripts/python scripts/prepare_agrodevices_readings.py LECTURAS.csv

Lee las unidades de data/agrodevices/units.parquet. La salida va a
data/agrodevices/readings.parquet (data/ no se sube a git) y el script imprime
cuántas filas y valores se descartan.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from irrigation_app.sources.agrodevices import read_readings  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data" / "agrodevices"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("readings", type=Path)
    parser.add_argument("--units", type=Path, default=DATA_DIR / "units.parquet")
    parser.add_argument("--output", type=Path, default=DATA_DIR / "readings.parquet")
    args = parser.parse_args()

    for path in (args.readings, args.units):
        if not path.exists():
            sys.exit(f"No existe el archivo {path}")

    readings, report = read_readings(args.readings, pd.read_parquet(args.units))

    print(f"Filas leídas:                    {report.rows_read:>11,}")
    print(f"  de unidades que no están:      {report.rows_other_units:>11,}")
    for reading_type, count in sorted(report.rows_excluded_by_type.items()):
        print(f"  excluidas por tipo {reading_type}:      {count:>11,}")
    print(f"Filas conservadas:               {report.rows_kept:>11,}")
    print(f"Valores guardados (instante y profundidad): {report.values_kept:>11,}")
    print(f"Valores descartados (0 o sin profundidad):  {report.values_discarded:>11,}")
    print("Tipos guardados:", readings["reading_type"].value_counts().to_dict())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    readings.to_parquet(args.output, index=False)
    print(f"\n{len(readings):,} lecturas guardadas en {args.output}")


if __name__ == "__main__":
    main()
