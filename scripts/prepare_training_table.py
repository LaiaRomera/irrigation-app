"""Construye la tabla de entrenamiento del modelo de humedad (sin meteorología todavía).

Quita los días de instalación de las sondas (como origen y como objetivo). Son la unión de la
regla automática, la lista manual y los días con lecturas SMMAdmin (de data/silver/daily.parquet). La exclusión por riego
marcado está lista en training_table.py pero aún no se aplica aquí: necesita la lluvia prevista
(fase 1) para no confundir lluvia con riego.

Uso (desde la raíz del proyecto, después de prepare_agrodevices_readings.py):
    .venv/Scripts/python scripts/prepare_training_table.py

Lee data/silver/readings.parquet y units.parquet, y escribe data/silver/snapshots.parquet
(humedad de las 05:00 UTC por unidad, día y profundidad) y data/gold/training_table.parquet (una
fila por sonda-profundidad, día de origen y horizonte de 1 a 7 días). Con --output-dir, ambos
archivos van a esa carpeta. data/ no se sube a git.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from irrigation_app.data_layers import DAILY_PATH, READINGS_PATH, SNAPSHOTS_PATH, TRAINING_TABLE_PATH, UNITS_PATH  # noqa: E402
from irrigation_app.irrigation_marks import admin_days, installation_days  # noqa: E402
from irrigation_app.training_table import build_training_table_with_counts, dawn_snapshots  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--readings", type=Path, default=READINGS_PATH)
    parser.add_argument("--units", type=Path, default=UNITS_PATH)
    parser.add_argument("--daily", type=Path, default=DAILY_PATH, help="datos diarios (para los días con SMMAdmin)")
    parser.add_argument("--output-dir", type=Path, default=None, help="si se indica, snapshots y tabla van a esta carpeta")
    args = parser.parse_args()

    for path in (args.readings, args.units, args.daily):
        if not path.exists():
            sys.exit(f"No existe el archivo {path}")

    snapshots = dawn_snapshots(pd.read_parquet(args.readings), pd.read_parquet(args.units))
    admin = admin_days(pd.read_parquet(args.daily, columns=["unit_id", "day", "n_admin"]))
    installation = installation_days(snapshots, admin=admin)
    automatic_and_manual = installation_days(snapshots)
    table, counts = build_training_table_with_counts(snapshots, installation=installation)

    print(f"{len(snapshots):,} valores de las 05:00 UTC -> {len(table):,} ejemplos de entrenamiento")
    print(
        f"Días de instalación excluidos: {len(installation):,} (de {installation['unit_id'].nunique()} unidades);"
        f" sin la regla SMMAdmin serían {len(automatic_and_manual):,} (de {automatic_and_manual['unit_id'].nunique()})."
        " Exclusión por riego: todavía no aplicada (falta la lluvia prevista)."
    )
    print("\nEjemplos por horizonte (change_*_nulled: campos que pasan a nulo por apoyarse en un día de instalación):")
    print(counts.to_string())
    summary = table.groupby("horizon_days").agg(
        ejemplos=("target_change", "size"),
        cambio_medio=("target_change", "mean"),
        error_persistencia=("target_change", lambda s: s.abs().mean()),
    )
    print("\nPor horizonte (error_persistencia = error medio de predecir «no cambia nada»):")
    print(summary.round(2).to_string())
    print(f"\nEjemplos sin cambio de 1 día anterior: {table['change_1d'].isna().mean():.1%}")

    if args.output_dir is not None:
        snapshots_path = args.output_dir / SNAPSHOTS_PATH.name
        table_path = args.output_dir / TRAINING_TABLE_PATH.name
    else:
        snapshots_path, table_path = SNAPSHOTS_PATH, TRAINING_TABLE_PATH
    for path in (snapshots_path, table_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    snapshots.to_parquet(snapshots_path, index=False)
    table.to_parquet(table_path, index=False)
    print(f"\nGuardado en {snapshots_path} y {table_path}")


if __name__ == "__main__":
    main()
