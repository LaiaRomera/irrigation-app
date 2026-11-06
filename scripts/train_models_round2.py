"""Ronda de mejora del modelo de humedad (declarada, 6 de noviembre de 2026).

Uso (desde la raíz del proyecto, con la tabla de oro, el reparto y los modelos vigentes ya generados):
    .venv/Scripts/python scripts/train_models_round2.py [--output-dir data/ml/round2]

Qué hace (ver `src/irrigation_app/models/round2.py`):
1. Calcula la lluvia y la ET0 de cada día del periodo en un archivo aparte (`daily_weather.parquet`);
   la tabla de oro vigente no se toca.
2. Con **entrenamiento y validación** elige la tendencia de la persistencia, el alfa de Ridge (rejilla
   ancha) y los hiperparámetros de LightGBM con las variables por día. No se reentrena con validación.
3. Mide todo en validación y en test (el test se mira por cuarta vez, declarado) y la métrica de
   decisión «¿acierta el día en que se cruza el umbral de riego?» (`models/crossing.py`).
4. Guarda en `data/ml/round2/` (no pisa `data/ml/metrics.csv` ni `data/ml/models/`).
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import lightgbm
import numpy as np
import pandas as pd
import sklearn

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from irrigation_app.data_layers import (  # noqa: E402
    DAILY_PATH, DATA_DIR, SNAPSHOTS_PATH, TRAINING_TABLE_PATH, UNITS_PATH,
    WEATHER_PREVIOUS_RUNS_PATH, WEATHER_UNIT_LOCATIONS_PATH,
)
from irrigation_app.features import daily_weather_features  # noqa: E402
from irrigation_app.irrigation_marks import admin_days, installation_days  # noqa: E402
from irrigation_app.models import crossing  # noqa: E402
from irrigation_app.models.round2 import fit_round2, load_current_models, predict_all, round2_metrics  # noqa: E402
from irrigation_app.models.split import SPLIT_PATH, TEST, assign_split, load_split  # noqa: E402
from irrigation_app.models.training import SEED  # noqa: E402
from irrigation_app.reference_plot import REFERENCE_PLOT  # noqa: E402

ML_DIR = DATA_DIR / "ml"
HORIZONS = list(range(1, 8))
P_VALUES = (0.4, 0.5, 0.6)  # 0,5 es el del motor; 0,4 y 0,6 solo para ver si la conclusión depende de él
REFERENCE_UNIT = REFERENCE_PLOT.unit_id


def shared_unit_ids(units: pd.DataFrame) -> list[int]:
    return sorted(int(u) for u in units.loc[units["coord_status"] == "shared", "unit_id"])


def crossing_report(
    table: pd.DataFrame, fitted: dict, current: dict, units: pd.DataFrame, snapshots: pd.DataFrame,
    installation: pd.DataFrame, cutoff: pd.Timestamp, shared: set[int],
) -> tuple[pd.DataFrame, dict]:
    """Métrica de cruce del umbral en el test, con anclajes confirmados (1168) y aproximados (resto)."""
    test = table[table["split"] == TEST]
    pieces, changes = [], {}
    for horizon in HORIZONS:
        rows = test[test["horizon_days"] == horizon]
        pieces.append(rows)
        for method, change in predict_all(rows, horizon, fitted, current).items():
            changes.setdefault(method, []).append(change)
    rows = pd.concat(pieces)
    changes = {m: np.concatenate(v) for m, v in changes.items()}
    zones = crossing.zone_depths_of(units)
    cases = crossing.build_cases(rows, zones, changes)

    means = crossing.zone_mean_by_unit(snapshots, zones, installation, cutoff)
    test_units = set(int(u) for u in rows["unit_id"].unique())
    proxy = {u: a for u in test_units if u in means and (a := crossing.proxy_anchors(means[u])) is not None}
    confirmed = {REFERENCE_UNIT: (REFERENCE_PLOT.anchors.upper.index, REFERENCE_PLOT.anchors.lower.index)}

    out, info = [], {"zone_depths_1168": zones.get(REFERENCE_UNIT), "proxy_anchors": {}, "units_with_proxy": len(proxy)}
    info["proxy_anchors"] = {str(u): [round(a[0], 2), round(a[1], 2)] for u, a in sorted(proxy.items())}
    for anchors_name, anchors in (("confirmed_1168", confirmed), ("proxy_percentiles", proxy)):
        for scenario, exclude in (("all", set()), ("no_shared", shared)):
            for p in P_VALUES:
                thresholds = {u: crossing.threshold_index(*a, p) for u, a in anchors.items() if u not in exclude}
                result = crossing.evaluate_crossings(cases, thresholds, p)
                out.append(result.assign(anchors=anchors_name, scenario=scenario))
    # Contraste: los anclajes aproximados de la 1168 frente a los confirmados (solo datos de entrenamiento).
    if REFERENCE_UNIT in proxy:
        info["proxy_vs_confirmed_1168"] = {
            "proxy_upper_lower": [round(v, 2) for v in proxy[REFERENCE_UNIT]],
            "confirmed_upper_lower": [round(v, 2) for v in confirmed[REFERENCE_UNIT]],
            "threshold_p05_proxy": round(crossing.threshold_index(*proxy[REFERENCE_UNIT], 0.5), 2),
            "threshold_p05_confirmed": round(crossing.threshold_index(*confirmed[REFERENCE_UNIT], 0.5), 2),
        }
    return pd.concat(out, ignore_index=True), info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=ML_DIR / "round2")
    parser.add_argument("--current-dir", type=Path, default=ML_DIR)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    for path in (TRAINING_TABLE_PATH, PROJECT_ROOT / SPLIT_PATH, UNITS_PATH, WEATHER_UNIT_LOCATIONS_PATH,
                 WEATHER_PREVIOUS_RUNS_PATH, SNAPSHOTS_PATH, DAILY_PATH, args.current_dir / "models"):
        if not path.exists():
            sys.exit(f"No existe {path}")
    if args.output_dir.resolve() == args.current_dir.resolve():
        sys.exit("El directorio de salida no puede ser el de los resultados vigentes")

    started = time.perf_counter()
    out = args.output_dir
    (out / "models").mkdir(parents=True, exist_ok=True)

    table = pd.read_parquet(TRAINING_TABLE_PATH)
    split = load_split(PROJECT_ROOT / SPLIT_PATH)
    unit_locations = pd.read_parquet(WEATHER_UNIT_LOCATIONS_PATH)
    units = pd.read_parquet(UNITS_PATH)
    shared = set(shared_unit_ids(units))
    labels = assign_split(table, unit_locations, split)
    table = table.assign(split=labels.to_numpy())
    table = table[table["split"].notna()]

    print("Variables meteorológicas por día (archivo aparte)")
    weather = pd.read_parquet(WEATHER_PREVIOUS_RUNS_PATH)
    daily = daily_weather_features(table, weather, unit_locations)
    daily.to_parquet(out / "daily_weather.parquet", index=False)
    print(f"  {len(daily):,} combinaciones unidad-origen-horizonte; nulos por columna: "
          + ", ".join(f"{c} {int(v)}" for c, v in daily.isna().sum().items() if c.endswith(("0", "6"))))
    daily["origin_day"] = pd.to_datetime(daily["origin_day"], utc=True)
    table = table.merge(daily, on=["unit_id", "origin_day", "horizon_days"], how="left", validate="many_to_one")

    print("Elección con entrenamiento y validación (el test no se mira)")
    fitted = fit_round2(table, HORIZONS, seed=args.seed)
    current = load_current_models(args.current_dir / "models", HORIZONS)

    print("Métricas (validación y test, una sola vez)")
    metrics = round2_metrics(table, fitted, current, HORIZONS, shared)
    snapshots = pd.read_parquet(SNAPSHOTS_PATH)
    admin = admin_days(pd.read_parquet(DAILY_PATH, columns=["unit_id", "day", "n_admin"]))
    installation = installation_days(snapshots, admin=admin)
    crossings, crossing_info = crossing_report(
        table, fitted, current, units, snapshots, installation, split.cutoff_day, shared
    )

    for method, by_horizon in fitted["models"].items():
        for horizon, candidate in by_horizon.items():
            joblib.dump(candidate.model, out / "models" / f"{method}_h{horizon}.joblib")
    metrics.to_csv(out / "metrics.csv", index=False, float_format="%.6f")
    fitted["searches"].to_csv(out / "search.csv", index=False, float_format="%.6f")
    crossings.to_csv(out / "crossing.csv", index=False, float_format="%.4f")
    summary = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": args.seed, "cutoff": split.cutoff,
        "note": "Ronda 2 declarada el 6-11-2026. El test se mira por cuarta vez. No se reentrena con la validación.",
        "versions": {"lightgbm": lightgbm.__version__, "scikit-learn": sklearn.__version__, "pandas": pd.__version__},
        "chosen": fitted["chosen"], "trend": fitted["trend_summary"], "crossing": crossing_info,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Guardado en {out}. Tiempo total: {time.perf_counter() - started:.0f} s")


if __name__ == "__main__":
    main()
