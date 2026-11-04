"""Construye la tabla de entrenamiento del modelo de humedad (sin meteorología todavía).

Uso (desde la raíz del proyecto, después de prepare_agrodevices_readings.py):
    .venv/Scripts/python scripts/prepare_training_table.py

Lee data/agrodevices/readings.parquet y units.parquet, y escribe data/agrodevices/snapshots.parquet
(humedad de las 05:00 UTC por unidad, día y profundidad) y training_table.parquet (una fila por
sonda-profundidad, día de origen y horizonte de 1 a 7 días). data/ no se sube a git.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from irrigation_app.training_table import build_training_table, dawn_snapshots  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data" / "agrodevices"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--readings", type=Path, default=DATA_DIR / "readings.parquet")
    parser.add_argument("--units", type=Path, default=DATA_DIR / "units.parquet")
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    for path in (args.readings, args.units):
        if not path.exists():
            sys.exit(f"No existe el archivo {path}")

    snapshots = dawn_snapshots(pd.read_parquet(args.readings), pd.read_parquet(args.units))
    table = build_training_table(snapshots)

    print(f"{len(snapshots):,} valores de las 05:00 UTC -> {len(table):,} ejemplos de entrenamiento")
    summary = table.groupby("horizon_days").agg(
        ejemplos=("target_change", "size"),
        cambio_medio=("target_change", "mean"),
        error_persistencia=("target_change", lambda s: s.abs().mean()),
    )
    print("\nPor horizonte (error_persistencia = error medio de predecir «no cambia nada»):")
    print(summary.round(2).to_string())
    print(f"\nEjemplos sin cambio de 1 día anterior: {table['change_1d'].isna().mean():.1%}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    snapshots.to_parquet(args.output_dir / "snapshots.parquet", index=False)
    table.to_parquet(args.output_dir / "training_table.parquet", index=False)
    print(f"\nGuardado en {args.output_dir}")


if __name__ == "__main__":
    main()
