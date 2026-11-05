"""Experimento completo de entrenamiento y evaluación (tareas 3.2 a 3.4).

Orden, que no se cambia mirando el test:
1. Por horizonte y método, se prueba la rejilla pequeña y se elige con el MAE de **validación**.
2. Con los modelos ya elegidos, se calculan las métricas de validación y de test (una sola vez).

Tres escenarios, todos con los mismos conjuntos del reparto (`split.json`):
- `all`: entrenado y evaluado con todas las unidades.
- `no_shared`: los mismos modelos, pero evaluados sin las unidades de coordenada `shared`
  (su ubicación no es fiable, así que su meteorología puede no ser la de su finca).
- `no_shared_retrained`: entrenado y evaluado sin esas unidades.

Además de la persistencia, Ridge y LightGBM, se entrenan Ridge y LightGBM sin las columnas
meteorológicas (`*_no_weather`) para medir cuánto de la mejora es meteorología.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable

import pandas as pd

from irrigation_app.features import WEATHER_FEATURE_COLUMNS
from irrigation_app.models.evaluation import evaluate_methods
from irrigation_app.models.split import TEST, TRAIN, VALIDATION
from irrigation_app.models.training import (
    BASE_FEATURE_COLUMNS,
    FEATURE_COLUMNS,
    LIGHTGBM,
    PERSISTENCE,
    RIDGE,
    SEED,
    TARGET,
    PersistenceModel,
    predict_change,
    search_model,
)

ALL, NO_SHARED, NO_SHARED_RETRAINED = "all", "no_shared", "no_shared_retrained"

# nombre del método -> (tipo de modelo, variables)
METHODS: dict[str, tuple[str, list[str]]] = {
    RIDGE: (RIDGE, FEATURE_COLUMNS),
    LIGHTGBM: (LIGHTGBM, FEATURE_COLUMNS),
    f"{RIDGE}_no_weather": (RIDGE, BASE_FEATURE_COLUMNS),
    f"{LIGHTGBM}_no_weather": (LIGHTGBM, BASE_FEATURE_COLUMNS),
}
MAIN_METHODS = (RIDGE, LIGHTGBM)
assert not set(WEATHER_FEATURE_COLUMNS) & set(BASE_FEATURE_COLUMNS)


def _fit_scope(
    table: pd.DataFrame,
    horizons: Iterable[int],
    methods: Iterable[str],
    grids: dict[str, list[dict[str, Any]]] | None,
    seed: int,
    n_jobs: int | None,
    log: Callable[[str], None],
    label: str,
) -> tuple[dict[str, dict[int, Any]], list[dict[str, Any]]]:
    """Elige y ajusta cada método en cada horizonte con entrenamiento y validación de `table`."""
    models: dict[str, dict[int, Any]] = {m: {} for m in methods}
    searches: list[dict[str, Any]] = []
    for horizon in horizons:
        subset = table[table["horizon_days"] == horizon]
        train, valid = subset[subset["split"] == TRAIN], subset[subset["split"] == VALIDATION]
        for method in methods:
            kind, features = METHODS[method]
            started = time.perf_counter()
            result = search_model(kind, train, valid, features, (grids or {}).get(kind), seed, n_jobs)
            models[method][horizon] = result.best
            for row in result.candidates:
                searches.append(
                    {
                        "trained_on": label,
                        "method": method,
                        "horizon_days": horizon,
                        "chosen": row["params"] == result.best.params,
                        **{f"param_{k}": v for k, v in row["params"].items()},
                        "valid_mae": row["valid_mae"],
                        "valid_rmse": row["valid_rmse"],
                        "best_iteration": row["best_iteration"],
                    }
                )
            log(
                f"  [{label}] h{horizon} {method}: validación MAE {result.best.valid_mae:.4f} con {result.best.params}"
                f" ({time.perf_counter() - started:.1f} s)"
            )
    return models, searches


def _metrics(
    table: pd.DataFrame,
    models: dict[str, dict[int, Any]],
    horizons: Iterable[int],
    split_name: str,
    scenario: str,
) -> pd.DataFrame:
    frames = []
    for horizon in horizons:
        rows = table[(table["split"] == split_name) & (table["horizon_days"] == horizon)]
        if rows.empty:
            continue
        predictions = {PERSISTENCE: predict_change(PersistenceModel(), rows)}
        for method, by_horizon in models.items():
            if horizon in by_horizon:
                _, features = METHODS[method]
                predictions[method] = predict_change(by_horizon[horizon].model, rows, features)
        frame = evaluate_methods(horizon, rows[TARGET].to_numpy(), predictions)
        frames.append(frame.assign(scenario=scenario, split=split_name))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def run_experiment(
    table: pd.DataFrame,
    labels: pd.Series,
    shared_units: Iterable[int],
    *,
    horizons: Iterable[int] = range(1, 8),
    grids: dict[str, list[dict[str, Any]]] | None = None,
    seed: int = SEED,
    n_jobs: int | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Elige hiperparámetros con la validación y devuelve modelos, búsquedas y métricas.

    `labels` es la salida de `assign_split` (train / validation / test / None) alineada con `table`.
    Devuelve `models` (escenario `all`), `metrics`, `searches` y `chosen` (hiperparámetros elegidos).
    """
    horizons = list(horizons)
    shared = set(shared_units)
    table = table.assign(split=labels.to_numpy())
    table = table[table["split"].notna() & table["horizon_days"].isin(horizons)]
    is_shared = table["unit_id"].isin(shared)

    log("Búsqueda de hiperparámetros (entrenado con todas las unidades)")
    models, searches = _fit_scope(table, horizons, METHODS, grids, seed, n_jobs, log, ALL)
    log("Búsqueda de hiperparámetros (entrenado sin las unidades shared)")
    retrained, searches_ns = _fit_scope(table[~is_shared], horizons, MAIN_METHODS, grids, seed, n_jobs, log, NO_SHARED)

    # A partir de aquí no se elige nada más: solo se miden los modelos ya fijados.
    metrics = []
    for split_name in (VALIDATION, TEST):
        metrics.append(_metrics(table, models, horizons, split_name, ALL))
        metrics.append(_metrics(table[~is_shared], models, horizons, split_name, NO_SHARED))
        metrics.append(_metrics(table[~is_shared], retrained, horizons, split_name, NO_SHARED_RETRAINED))
    metrics_frame = pd.concat([m for m in metrics if not m.empty], ignore_index=True)
    columns = ["scenario", "split", "method", "horizon_days", "n", "mae", "rmse", "bias", "mae_improvement_pct", "rmse_improvement_pct"]

    chosen = {
        label: {
            method: {
                str(h): {"params": c.params, "best_iteration": c.best_iteration, "valid_mae": c.valid_mae}
                for h, c in by_horizon.items()
            }
            for method, by_horizon in group.items()
        }
        for label, group in ((ALL, models), (NO_SHARED, retrained))
    }
    return {
        "models": models,
        "metrics": metrics_frame[columns],
        "searches": pd.DataFrame(searches + searches_ns),
        "chosen": chosen,
    }
