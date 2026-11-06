"""Ronda de mejora del modelo de humedad (declarada, 6 de noviembre de 2026).

Una sola ronda, corta, aprobada por Laia. Qué cambia respecto al experimento vigente (`experiment.py`):
1. **Lluvia y ET0 de cada día del periodo** (hasta 7 columnas por variable, nulas si el horizonte es
   menor), además de las sumas y medias de siempre (`features.daily_weather_features`).
2. **Ridge con una rejilla de alfa más ancha** (el óptimo vigente tocaba el borde, 100).
3. **Persistencia con tendencia reciente** como referencia más exigente (`trend.py`); la persistencia
   simple se mantiene como la vara vigente.

Reglas que no cambian: hiperparámetros y elecciones **solo con la validación**; el test se mide una
vez con todo ya fijado; no se reentrena con la validación. Los modelos vigentes (`data/ml/models/`) se
cargan para compararlos, nunca se tocan: los nuevos se guardan en `data/ml/round2/`.

Métodos (todos con los mismos ejemplos de cada conjunto):
- `persistence`, `persistence_trend`, `persistence_drift`: referencias (la tendencia resulta idéntica a
  la persistencia en los datos reales; la deriva constante es la referencia extra que sí se mueve).
- `ridge_current`, `lightgbm_current`: los modelos vigentes (variables de siempre).
- `ridge_wide`: Ridge con las variables de siempre y la rejilla ancha (aísla el efecto del alfa).
- `ridge_daily`, `lightgbm_daily`: con las variables por día. Estos dos son los candidatos nuevos.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Iterable

import joblib
import numpy as np
import pandas as pd

from irrigation_app.features import daily_weather_columns
from irrigation_app.models.evaluation import BASELINE, METRIC_COLUMNS, error_metrics, improvement_pct
from irrigation_app.models.split import TEST, TRAIN, VALIDATION
from irrigation_app.models.training import (
    FEATURE_COLUMNS,
    LIGHTGBM,
    RIDGE,
    SEED,
    TARGET,
    PersistenceModel,
    predict_change,
    search_model,
)
from irrigation_app.models.trend import DRIFT, TREND, fit_drift, fit_trend

# La primera pasada (hasta 100.000) volvió a tocar el borde en todos los horizontes; se amplió de forma
# mecánica hasta 10 millones, donde Ridge ya es casi un modelo constante (no se amplía más).
RIDGE_ALPHAS_WIDE = (
    0.1, 1.0, 10.0, 100.0, 300.0, 1000.0, 3000.0, 10000.0, 30000.0, 100000.0, 300000.0, 1000000.0, 3000000.0, 10000000.0,
)
RIDGE_WIDE_GRID = [{"alpha": alpha} for alpha in RIDGE_ALPHAS_WIDE]

RIDGE_WIDE, RIDGE_DAILY, LIGHTGBM_DAILY = "ridge_wide", "ridge_daily", "lightgbm_daily"
RIDGE_CURRENT, LIGHTGBM_CURRENT = "ridge_current", "lightgbm_current"
NEW_METHODS = (RIDGE_WIDE, RIDGE_DAILY, LIGHTGBM_DAILY)
CURRENT_METHODS = {RIDGE_CURRENT: RIDGE, LIGHTGBM_CURRENT: LIGHTGBM}
ALL, NO_SHARED = "all", "no_shared"
METRIC_COLUMNS_R2 = [
    "scenario", "split", *METRIC_COLUMNS, "mae_improvement_vs_trend_pct", "rmse_improvement_vs_trend_pct",
    "mae_improvement_vs_drift_pct", "rmse_improvement_vs_drift_pct",
]


def method_kind_and_features(method: str, horizon_days: int) -> tuple[str, list[str]]:
    """Tipo de modelo y variables de un método nuevo para un horizonte (solo usa los días que existen)."""
    if method == RIDGE_WIDE:
        return RIDGE, FEATURE_COLUMNS
    daily = daily_weather_columns(horizon_days)
    if method == RIDGE_DAILY:
        return RIDGE, FEATURE_COLUMNS + daily
    if method == LIGHTGBM_DAILY:
        return LIGHTGBM, FEATURE_COLUMNS + daily
    raise ValueError(f"Método desconocido: {method}")


def load_current_models(models_dir: Path | str, horizons: Iterable[int]) -> dict[str, dict[int, Any]]:
    """Carga los modelos vigentes (solo lectura) de `data/ml/models/`."""
    models_dir = Path(models_dir)
    return {
        name: {h: joblib.load(models_dir / f"{kind}_h{h}.joblib") for h in horizons}
        for name, kind in CURRENT_METHODS.items()
    }


def fit_round2(
    table: pd.DataFrame,
    horizons: Iterable[int],
    *,
    seed: int = SEED,
    n_jobs: int | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Elige y ajusta los métodos nuevos y la tendencia con entrenamiento y validación de `table`.

    `table` necesita la columna `split` y las columnas por día. No mira el test.
    """
    models: dict[str, dict[int, Any]] = {m: {} for m in NEW_METHODS}
    trends: dict[int, Any] = {}
    drifts: dict[int, Any] = {}
    trend_summary: dict[str, Any] = {}
    searches: list[dict[str, Any]] = []
    chosen: dict[str, Any] = {m: {} for m in NEW_METHODS}
    for horizon in horizons:
        subset = table[table["horizon_days"] == horizon]
        train, valid = subset[subset["split"] == TRAIN], subset[subset["split"] == VALIDATION]
        trends[horizon], trend_summary[str(horizon)] = fit_trend(train, valid, horizon)
        drifts[horizon] = fit_drift(train)
        log(f"  h{horizon} {TREND}: {trend_summary[str(horizon)]}; {DRIFT}: {drifts[horizon].offset:.4f}")
        for method in NEW_METHODS:
            kind, features = method_kind_and_features(method, horizon)
            grid = RIDGE_WIDE_GRID if kind == RIDGE else None
            started = time.perf_counter()
            result = search_model(kind, train, valid, features, grid, seed, n_jobs)
            models[method][horizon] = result.best
            chosen[method][str(horizon)] = {
                "params": result.best.params, "best_iteration": result.best.best_iteration,
                "valid_mae": result.best.valid_mae,
            }
            for row in result.candidates:
                searches.append(
                    {
                        "method": method, "horizon_days": horizon, "chosen": row["params"] == result.best.params,
                        **{f"param_{k}": v for k, v in row["params"].items()},
                        "valid_mae": row["valid_mae"], "valid_rmse": row["valid_rmse"],
                        "best_iteration": row["best_iteration"],
                    }
                )
            log(
                f"  h{horizon} {method}: validación MAE {result.best.valid_mae:.4f} con {result.best.params}"
                f" ({time.perf_counter() - started:.1f} s)"
            )
    return {
        "models": models, "trends": trends, "drifts": drifts, "searches": pd.DataFrame(searches),
        "chosen": chosen, "trend_summary": trend_summary,
    }


def predict_all(
    rows: pd.DataFrame,
    horizon_days: int,
    fitted: dict[str, Any],
    current: dict[str, dict[int, Any]] | None,
) -> dict[str, np.ndarray]:
    """Cambio previsto por cada método para las filas de un horizonte (persistencia primero)."""
    predictions = {
        BASELINE: predict_change(PersistenceModel(), rows),
        TREND: fitted["trends"][horizon_days].predict(rows),
        DRIFT: fitted["drifts"][horizon_days].predict(rows),
    }
    for method, by_horizon in fitted["models"].items():
        _, features = method_kind_and_features(method, horizon_days)
        predictions[method] = predict_change(by_horizon[horizon_days].model, rows, features)
    for method, by_horizon in (current or {}).items():
        predictions[method] = predict_change(by_horizon[horizon_days], rows)
    return predictions


def metrics_for_rows(horizon_days: int, actual: np.ndarray, predictions: dict[str, np.ndarray]) -> pd.DataFrame:
    """Métricas de varios métodos, con la mejora frente a la persistencia, la tendencia y la deriva."""
    rows = [
        {"method": m, "horizon_days": int(horizon_days), **error_metrics(actual, p)} for m, p in predictions.items()
    ]
    frame = pd.DataFrame(rows)
    for suffix, reference in (("", BASELINE), ("_vs_trend", TREND), ("_vs_drift", DRIFT)):
        base = frame[frame["method"] == reference].iloc[0]
        frame[f"mae_improvement{suffix}_pct"] = [improvement_pct(base["mae"], v) for v in frame["mae"]]
        frame[f"rmse_improvement{suffix}_pct"] = [improvement_pct(base["rmse"], v) for v in frame["rmse"]]
    return frame


def round2_metrics(
    table: pd.DataFrame,
    fitted: dict[str, Any],
    current: dict[str, dict[int, Any]] | None,
    horizons: Iterable[int],
    shared_units: Iterable[int],
) -> pd.DataFrame:
    """Métricas de validación y de test, con todas las unidades y sin las `shared` (mismos modelos)."""
    shared = set(shared_units)
    frames = []
    for split_name in (VALIDATION, TEST):
        for scenario, keep_shared in ((ALL, True), (NO_SHARED, False)):
            part = table[table["split"] == split_name]
            if not keep_shared:
                part = part[~part["unit_id"].isin(shared)]
            for horizon in horizons:
                rows = part[part["horizon_days"] == horizon]
                if rows.empty:
                    continue
                predictions = predict_all(rows, horizon, fitted, current)
                frame = metrics_for_rows(horizon, rows[TARGET].to_numpy(), predictions)
                frames.append(frame.assign(scenario=scenario, split=split_name))
    return pd.concat(frames, ignore_index=True)[METRIC_COLUMNS_R2]
