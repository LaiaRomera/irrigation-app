"""Entrena y evalúa los modelos de humedad (tarea 3.4).

Uso (desde la raíz del proyecto, después de prepare_training_table.py y de generar data/ml/split.json):
    .venv/Scripts/python scripts/train_models.py [--output-dir data/ml] [--seed 42]

Qué hace:
1. Lee la tabla de oro y el reparto (`data/ml/split.json`, corte de fechas y ubicaciones de test).
2. Por horizonte (1 a 7 días) elige los hiperparámetros de Ridge y LightGBM con una búsqueda
   pequeña, usando solo la validación.
3. Mide persistencia, Ridge y LightGBM (MAE, RMSE y sesgo) en validación y en test, una sola vez,
   con todas las unidades y sin las de coordenada `shared`. También mide los dos modelos sin la
   meteorología, para saber cuánto de la mejora se debe al tiempo.
4. Guarda los modelos (`models/`), las métricas (`metrics.csv`), la búsqueda (`search.csv`) y un
   resumen con los hiperparámetros elegidos (`models/summary.json`).

Todo con semilla fija: dos ejecuciones dan lo mismo. `data/` no se sube a git.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import lightgbm
import pandas as pd
import sklearn

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from irrigation_app.data_layers import DATA_DIR, TRAINING_TABLE_PATH, UNITS_PATH, WEATHER_UNIT_LOCATIONS_PATH  # noqa: E402
from irrigation_app.models.experiment import run_experiment  # noqa: E402
from irrigation_app.models.split import SPLIT_PATH, assign_split, load_split  # noqa: E402
from irrigation_app.models.training import SEED  # noqa: E402

ML_DIR = DATA_DIR / "ml"


def shared_unit_ids(units_path: Path) -> list[int]:
    """Unidades cuya coordenada es compartida por varios clientes (`coord_status == "shared"`)."""
    units = pd.read_parquet(units_path, columns=["unit_id", "coord_status"])
    return sorted(int(u) for u in units.loc[units["coord_status"] == "shared", "unit_id"])


def print_summary(metrics: pd.DataFrame) -> None:
    for (scenario, split_name), group in metrics.groupby(["scenario", "split"], sort=False):
        print(f"\n== {scenario} / {split_name}: MAE por horizonte (días) ==")
        print(group.pivot(index="method", columns="horizon_days", values="mae").round(3).to_string())
        print("mejora del MAE frente a la persistencia (%):")
        print(group.pivot(index="method", columns="horizon_days", values="mae_improvement_pct").round(1).to_string())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--table", type=Path, default=TRAINING_TABLE_PATH)
    parser.add_argument("--split", type=Path, default=PROJECT_ROOT / SPLIT_PATH)
    parser.add_argument("--units", type=Path, default=UNITS_PATH)
    parser.add_argument("--unit-locations", type=Path, default=WEATHER_UNIT_LOCATIONS_PATH)
    parser.add_argument("--output-dir", type=Path, default=ML_DIR)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    for path in (args.table, args.split, args.units, args.unit_locations):
        if not path.exists():
            sys.exit(f"No existe el archivo {path}")

    started = time.perf_counter()
    table = pd.read_parquet(args.table)
    split = load_split(args.split)
    labels = assign_split(table, pd.read_parquet(args.unit_locations), split)
    shared = shared_unit_ids(args.units)
    print(
        f"{len(table):,} ejemplos; corte {split.cutoff}; {len(split.holdout_locations)} ubicaciones de test; "
        f"{len(shared)} unidades shared. Conjuntos: " + ", ".join(f"{k} {v:,}" for k, v in labels.value_counts().items())
    )

    result = run_experiment(table, labels, shared, seed=args.seed)
    metrics = result["metrics"]
    print_summary(metrics)

    out = args.output_dir
    (out / "models").mkdir(parents=True, exist_ok=True)
    for method, by_horizon in result["models"].items():
        for horizon, candidate in by_horizon.items():
            joblib.dump(candidate.model, out / "models" / f"{method}_h{horizon}.joblib")
    metrics.to_csv(out / "metrics.csv", index=False, float_format="%.6f")
    result["searches"].to_csv(out / "search.csv", index=False, float_format="%.6f")
    summary = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": args.seed,
        "cutoff": split.cutoff,
        "split_holdout_locations": split.holdout_locations,
        "shared_units": shared,
        "examples": {str(k): int(v) for k, v in labels.value_counts().items()},
        "versions": {"lightgbm": lightgbm.__version__, "scikit-learn": sklearn.__version__, "pandas": pd.__version__},
        "chosen": result["chosen"],
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }
    (out / "models" / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nGuardado en {out} ({len(metrics)} filas de métricas). Tiempo total: {time.perf_counter() - started:.0f} s")


if __name__ == "__main__":
    main()
