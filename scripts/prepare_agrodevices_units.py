"""Prepara la tabla de unidades de Agrodevices y la guarda en Parquet.

Uso (desde la raíz del proyecto):
    .venv/Scripts/python scripts/prepare_agrodevices_units.py PLAYER_DATA.csv PROFILER_SETUP.csv [LECTURAS.csv]

Si se indica el CSV de lecturas, solo se conservan las unidades que aparecen en él.
La salida va a data/agrodevices/units.parquet (data/ no se sube a git) y el script imprime
cuántas unidades quita cada filtro.
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from irrigation_app.sources.agrodevices import (  # noqa: E402
    build_units,
    read_devices,
    read_profiles,
    read_reading_unit_ids,
)

DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "agrodevices" / "units.parquet"
MAX_IDS_SHOWN = 15


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("player_data", type=Path)
    parser.add_argument("profiler_setup", type=Path)
    parser.add_argument("readings", type=Path, nargs="?")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    for path in (args.player_data, args.profiler_setup, args.readings):
        if path is not None and not path.exists():
            sys.exit(f"No existe el archivo {path}")

    reading_ids = read_reading_unit_ids(args.readings) if args.readings else None
    units, steps = build_units(read_devices(args.player_data), read_profiles(args.profiler_setup), reading_ids)

    print(f"{'Filtro':<42}{'antes':>7}{'después':>9}  quitadas")
    for step in steps:
        shown = ", ".join(str(u) for u in step.removed_ids[:MAX_IDS_SHOWN])
        more = f" … (+{len(step.removed_ids) - MAX_IDS_SHOWN})" if len(step.removed_ids) > MAX_IDS_SHOWN else ""
        print(f"{step.name:<42}{step.units_before:>7}{step.units_after:>9}  {shown}{more}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    units.to_parquet(args.output, index=False)
    print(f"\n{len(units)} unidades guardadas en {args.output}")
    print("Coordenadas:", units["coord_status"].value_counts().to_dict())
    print("Número de profundidades:", units["n_depths"].value_counts().sort_index().to_dict())


if __name__ == "__main__":
    main()
