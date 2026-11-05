"""Construye la tabla de entrenamiento del modelo de humedad, con meteorología y sin riegos.

Pasos (tareas 1.4, 2.3 y 2.5):
1. Fotos de las 05:00 UTC por unidad, día y profundidad (plata, snapshots.parquet).
2. Exclusión de los días de instalación (regla automática + lista manual + días con SMMAdmin).
3. Marcado de posibles riegos (subida del índice sin lluvia que la explique) y exclusión, por
   horizonte, de los ejemplos cuyo periodo a predecir contiene un riego marcado.
4. Unión de la meteorología prevista (días origen..origen+N-1, cada uno con su antelación). Un dato
   que falta deja la variable nula y `weather_incomplete = True`; esos ejemplos no se quitan.

Uso (desde la raíz del proyecto, después de prepare_agrodevices_readings.py y download_weather.py):
    .venv/Scripts/python scripts/prepare_training_table.py [--rise-threshold 2 --rain-limit 2]

Lee data/silver/ (readings, units, daily y la meteorología) y escribe data/silver/snapshots.parquet,
data/gold/training_table.parquet y, junto a ella, data/gold/training_table_params.json con los
parámetros del riego y los recuentos. Con --output-dir, los tres van a esa carpeta (útil para
probar parámetros sin tocar la tabla de oro). data/ no se sube a git.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from irrigation_app.data_layers import (  # noqa: E402
    DAILY_PATH,
    READINGS_PATH,
    SNAPSHOTS_PATH,
    TRAINING_PARAMS_PATH,
    TRAINING_TABLE_PATH,
    UNITS_PATH,
    WEATHER_PREVIOUS_RUNS_PATH,
    WEATHER_UNIT_LOCATIONS_PATH,
)
from irrigation_app.features import INCOMPLETE_COLUMN, add_weather_features  # noqa: E402
from irrigation_app.irrigation_marks import (  # noqa: E402
    DEPTH_AGGREGATIONS,
    INSTALL_MAX_DAYS,
    INSTALL_THRESHOLD,
    IRRIGATION_RAIN_LIMIT_MM,
    IRRIGATION_RISE_THRESHOLD,
    MANUAL_INSTALLATION,
    admin_days,
    installation_days,
    interval_rain,
    possible_irrigation,
)
from irrigation_app.training_table import build_training_table_with_counts, dawn_snapshots  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--readings", type=Path, default=READINGS_PATH)
    parser.add_argument("--units", type=Path, default=UNITS_PATH)
    parser.add_argument("--daily", type=Path, default=DAILY_PATH, help="datos diarios (para los días con SMMAdmin)")
    parser.add_argument("--weather", type=Path, default=WEATHER_PREVIOUS_RUNS_PATH)
    parser.add_argument("--unit-locations", type=Path, default=WEATHER_UNIT_LOCATIONS_PATH)
    parser.add_argument("--rise-threshold", type=float, default=IRRIGATION_RISE_THRESHOLD, help="subida mínima del índice (puntos) para marcar un riego")
    parser.add_argument("--rain-limit", type=float, default=IRRIGATION_RAIN_LIMIT_MM, help="lluvia (mm) a partir de la cual la subida se atribuye a la lluvia")
    parser.add_argument("--depth-agg", choices=DEPTH_AGGREGATIONS, default="mean", help="cómo juntar las profundidades al marcar el riego")
    parser.add_argument("--output-dir", type=Path, default=None, help="si se indica, snapshots, tabla y parámetros van a esta carpeta")
    args = parser.parse_args()

    for path in (args.readings, args.units, args.daily, args.weather, args.unit_locations):
        if not path.exists():
            sys.exit(f"No existe el archivo {path}")

    snapshots = dawn_snapshots(pd.read_parquet(args.readings), pd.read_parquet(args.units))
    weather = pd.read_parquet(args.weather)
    unit_locations = pd.read_parquet(args.unit_locations)

    admin = admin_days(pd.read_parquet(args.daily, columns=["unit_id", "day", "n_admin"]))
    installation = installation_days(snapshots, admin=admin)
    irrigation = possible_irrigation(
        snapshots, interval_rain(weather, unit_locations), args.rise_threshold, args.rain_limit, args.depth_agg
    )
    table, counts = build_training_table_with_counts(snapshots, installation=installation, irrigation=irrigation)
    table = add_weather_features(table, weather, unit_locations)
    counts["weather_incomplete"] = table.groupby("horizon_days")[INCOMPLETE_COLUMN].sum().astype(int)

    marked = irrigation[irrigation["possible_irrigation"]]
    print(f"{len(snapshots):,} valores de las 05:00 UTC -> {len(table):,} ejemplos de entrenamiento")
    print(f"Días de instalación excluidos: {len(installation):,} (de {installation['unit_id'].nunique()} unidades).")
    print(
        f"Riegos marcados (subida de más de {args.rise_threshold:g} puntos, lluvia < {args.rain_limit:g} mm, "
        f"profundidades por «{args.depth_agg}»): {len(marked):,} días de unidad, en {marked['unit_id'].nunique()} unidades."
    )
    print("\nEjemplos por horizonte (final = inicial - instalación - riego; change_*_nulled: campos que pasan a nulo")
    print("por apoyarse en un día de instalación; weather_incomplete: con meteorología incompleta, marcados y no quitados):")
    print(counts.to_string())
    print(f"\nEjemplos con meteorología incompleta: {int(counts['weather_incomplete'].sum()):,} de {len(table):,}")
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
        params_path = args.output_dir / TRAINING_PARAMS_PATH.name
    else:
        snapshots_path, table_path, params_path = SNAPSHOTS_PATH, TRAINING_TABLE_PATH, TRAINING_PARAMS_PATH
    for path in (snapshots_path, table_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    snapshots.to_parquet(snapshots_path, index=False)
    table.to_parquet(table_path, index=False)

    params = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "irrigation": {
            "rise_threshold_points": args.rise_threshold,
            "rain_limit_mm": args.rain_limit,
            "depth_aggregation": args.depth_agg,
            "rain_source": "máximo de la lluvia del día d-1 y del día d, previsión con antelación 0 (aproximación con datos diarios en UTC completo, no de 05:00 a 05:00)",
            "marked_unit_days": int(len(marked)),
            "marked_units": int(marked["unit_id"].nunique()),
        },
        "installation": {
            "threshold": INSTALL_THRESHOLD,
            "max_days": INSTALL_MAX_DAYS,
            "manual_units": sorted(MANUAL_INSTALLATION),
            "days": int(len(installation)),
            "units": int(installation["unit_id"].nunique()),
        },
        "counts_by_horizon": json.loads(counts.to_json(orient="index")),
        "examples": int(len(table)),
    }
    params_path.write_text(json.dumps(params, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nGuardado en {snapshots_path}, {table_path} y {params_path}")


if __name__ == "__main__":
    main()
