"""Registra en `ml_model` los modelos entrenados de `data/ml/` y los deja activos (tarea 4.3, contrato C4).

Uso (desde la raíz del proyecto):
    .venv/Scripts/python scripts/register_models.py --env-file .env.docker-pg              # PostgreSQL local
    .venv/Scripts/python scripts/register_models.py --env-file .env.docker-pg --dry-run    # solo muestra lo que haría
    .venv/Scripts/python scripts/register_models.py --env-file .env --allow-remote         # Aiven

Por defecto registra el LightGBM de cada horizonte (1 a 7), el modelo vigente, con sus hiperparámetros
(`data/ml/models/summary.json`), sus métricas frente a la persistencia (`data/ml/metrics.csv`), las
fechas de entrenamiento (de la tabla de oro y `data/ml/split.json`) y la versión del código. Es
idempotente: registrar dos veces el mismo modelo no duplica filas. Con `--no-activate` los registra sin
activarlos; activar uno desactiva el anterior del mismo horizonte.

Los modelos de la ronda de mejora 2 (`data/ml/round2/`) no se registran: no se adoptaron.
Sin `--allow-remote` solo escribe en un servidor local. Nunca imprime la contraseña.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import psycopg

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from irrigation_app import db  # noqa: E402
from irrigation_app.data_layers import DATA_DIR, TRAINING_TABLE_PATH, WEATHER_UNIT_LOCATIONS_PATH  # noqa: E402
from irrigation_app.models.registry import build_records, current_code_version, register_model, training_date_ranges  # noqa: E402
from irrigation_app.models.split import SPLIT_PATH, TRAIN, assign_split, load_split  # noqa: E402

ML_DIR = DATA_DIR / "ml"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", default=".env", help="archivo con las variables DB_* (por defecto .env)")
    parser.add_argument("--ml-dir", type=Path, default=ML_DIR)
    parser.add_argument("--algorithms", nargs="+", default=["lightgbm"], choices=["lightgbm", "ridge", "persistence"])
    parser.add_argument("--no-activate", action="store_true", help="registra sin activar")
    parser.add_argument("--dry-run", action="store_true", help="solo muestra lo que registraría; no se conecta")
    parser.add_argument("--allow-remote", action="store_true", help="permite escribir en un servidor que no sea local (Aiven)")
    args = parser.parse_args()

    summary_path, metrics_path = args.ml_dir / "models" / "summary.json", args.ml_dir / "metrics.csv"
    split_path = PROJECT_ROOT / SPLIT_PATH
    for path in (summary_path, metrics_path, split_path, TRAINING_TABLE_PATH, WEATHER_UNIT_LOCATIONS_PATH):
        if not path.exists():
            sys.exit(f"No existe el archivo {path}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = pd.read_csv(metrics_path)
    table = pd.read_parquet(TRAINING_TABLE_PATH, columns=["unit_id", "horizon_days", "origin_day", "depth_cm"])
    labels = assign_split(table, pd.read_parquet(WEATHER_UNIT_LOCATIONS_PATH), load_split(split_path))
    ranges = training_date_ranges(table, labels, TRAIN)
    depths = sorted(int(d) for d in table.loc[labels.to_numpy() == TRAIN, "depth_cm"].unique())
    print(f"Profundidades vistas al entrenar: {depths}")
    records = build_records(
        summary, metrics, algorithms=args.algorithms, models_dir=args.ml_dir / "models",
        train_ranges=ranges, code_version=current_code_version(), training_depths=depths,
    )

    print(f"{len(records)} modelos: {', '.join(args.algorithms)} x horizontes 1 a 7 (entrenados el {summary['generated_at']}).")
    for r in records:
        typical = (r.metrics or {}).get("typical_error") or {}
        mae = typical.get("mae")
        print(
            f"  h{r.horizon_days} {r.algorithm}: entrenado {r.train_from} a {r.train_to}; MAE en test "
            f"{'sin dato' if mae is None else f'{mae:.3f}'}; archivo {r.artifact_path}"
        )
    if args.dry_run:
        print("Prueba en seco: no se escribe nada.")
        return

    try:
        config = db.load_config(args.env_file)
    except db.DatabaseConfigError as error:
        sys.exit(str(error))
    print(f"Destino: {db.describe(config)}")
    if config.host not in LOCAL_HOSTS and not args.allow_remote:
        sys.exit("El destino no es local. Para escribir en Aiven hay que pasar --allow-remote (decisión de Laia).")
    try:
        connection = db.connect(config)
    except psycopg.OperationalError as error:
        sys.exit(f"No se pudo conectar: {error}")
    with connection:
        ids = [register_model(connection, record, activate=not args.no_activate) for record in records]
    print(f"Registrados {len(ids)} modelos (model_id {min(ids)} a {max(ids)}){'' if args.no_activate else ', activos'}.")


if __name__ == "__main__":
    main()
