"""Modelos de humedad: persistencia, Ridge y LightGBM (tarea 3.2, decisión D4).

Hay un modelo por horizonte (1 a 7 días). Todos predicen el **cambio** de humedad
(`target_change`, es decir, índice del día objetivo menos `base_index`) y el nivel se obtiene
sumando ese cambio al índice de origen (`predict_level`). La persistencia es «no cambia nada»:
cambio 0, nivel igual al origen. Es la vara de medir de los otros dos.

- **Ridge**: imputa los nulos con la mediana (de entrenamiento), añade una columna indicadora de
  falta por cada variable que tenía nulos y estandariza antes de ajustar la regresión.
- **LightGBM**: admite nulos tal cual; se detiene solo cuando la validación deja de mejorar.

La búsqueda de hiperparámetros es pequeña (`SEARCH_GRIDS`) y se decide solo con la validación.
Con las mismas semillas y los mismos datos, el resultado es el mismo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor, early_stopping
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from irrigation_app.features import WEATHER_FEATURE_COLUMNS

SEED = 42

TARGET = "target_change"
BASE_COLUMN = "base_index"

# Variables que no son meteorología: la sonda, su historia reciente y la época del año.
BASE_FEATURE_COLUMNS = ["depth_cm", "base_index", "change_1d", "change_3d", "doy_sin", "doy_cos"]
FEATURE_COLUMNS = BASE_FEATURE_COLUMNS + WEATHER_FEATURE_COLUMNS

PERSISTENCE, RIDGE, LIGHTGBM = "persistence", "ridge", "lightgbm"

# Búsqueda pequeña: unos pocos valores razonables, no una exploración exhaustiva.
SEARCH_GRIDS: dict[str, list[dict[str, Any]]] = {
    RIDGE: [{"alpha": alpha} for alpha in (0.1, 1.0, 10.0, 100.0)],
    LIGHTGBM: [
        {"num_leaves": leaves, "min_child_samples": min_child, "learning_rate": 0.05}
        for leaves in (15, 63)
        for min_child in (50, 500)
    ],
}
LIGHTGBM_MAX_TREES = 1000
LIGHTGBM_PATIENCE = 50


class PersistenceModel:
    """Predice «sin cambio»: el nivel de mañana es el de hoy."""

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        return np.zeros(len(features))


def predict_level(base_index: Iterable[float], predicted_change: Iterable[float]) -> np.ndarray:
    """Nivel previsto = índice de origen + cambio previsto (70 y −2,5 dan 67,5)."""
    return np.asarray(base_index, dtype=float) + np.asarray(predicted_change, dtype=float)


def _matrix(frame: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    return frame[features].astype("float64")


def make_ridge(alpha: float) -> Pipeline:
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
            ("scale", StandardScaler()),
            ("ridge", Ridge(alpha=alpha)),
        ]
    )


def make_lightgbm(params: dict[str, Any], seed: int = SEED, n_jobs: int | None = None) -> LGBMRegressor:
    return LGBMRegressor(
        objective="regression",
        n_estimators=params.get("n_estimators", LIGHTGBM_MAX_TREES),
        learning_rate=params.get("learning_rate", 0.05),
        num_leaves=params.get("num_leaves", 31),
        min_child_samples=params.get("min_child_samples", 50),
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.8,
        random_state=seed,
        deterministic=True,
        force_row_wise=True,
        n_jobs=n_jobs,
        verbose=-1,
    )


@dataclass
class Candidate:
    """Un modelo ajustado con unos hiperparámetros y su error en validación."""

    kind: str
    params: dict[str, Any]
    model: Any
    valid_mae: float
    valid_rmse: float
    best_iteration: int | None = None


@dataclass
class SearchResult:
    best: Candidate
    candidates: list[dict[str, Any]] = field(default_factory=list)  # una fila por combinación probada


def fit_candidate(
    kind: str,
    params: dict[str, Any],
    train: pd.DataFrame,
    valid: pd.DataFrame,
    features: list[str],
    seed: int = SEED,
    n_jobs: int | None = None,
) -> Candidate:
    """Ajusta un modelo con el entrenamiento y mide su error en la validación."""
    x_train, y_train = _matrix(train, features), train[TARGET].to_numpy(dtype=float)
    x_valid, y_valid = _matrix(valid, features), valid[TARGET].to_numpy(dtype=float)
    best_iteration = None
    if kind == RIDGE:
        model = make_ridge(params["alpha"]).fit(x_train, y_train)
    elif kind == LIGHTGBM:
        model = make_lightgbm(params, seed, n_jobs)
        model.fit(
            x_train,
            y_train,
            eval_X=x_valid,
            eval_y=y_valid,
            eval_metric="l1",
            callbacks=[early_stopping(LIGHTGBM_PATIENCE, first_metric_only=False, verbose=False)],
        )
        best_iteration = int(model.best_iteration_ or model.n_estimators)
    else:
        raise ValueError(f"Método desconocido: {kind}")
    error = model.predict(x_valid) - y_valid
    return Candidate(
        kind=kind,
        params=dict(params),
        model=model,
        valid_mae=float(np.abs(error).mean()),
        valid_rmse=float(np.sqrt((error**2).mean())),
        best_iteration=best_iteration,
    )


def search_model(
    kind: str,
    train: pd.DataFrame,
    valid: pd.DataFrame,
    features: list[str] | None = None,
    grid: list[dict[str, Any]] | None = None,
    seed: int = SEED,
    n_jobs: int | None = None,
) -> SearchResult:
    """Prueba cada combinación de la rejilla y se queda con la de menor MAE en validación.

    Si dos empatan, gana la primera de la rejilla (resultado estable).
    """
    features = features or FEATURE_COLUMNS
    grid = grid if grid is not None else SEARCH_GRIDS[kind]
    best: Candidate | None = None
    rows = []
    for params in grid:
        candidate = fit_candidate(kind, params, train, valid, features, seed, n_jobs)
        rows.append(
            {
                "params": dict(params),
                "valid_mae": candidate.valid_mae,
                "valid_rmse": candidate.valid_rmse,
                "best_iteration": candidate.best_iteration,
            }
        )
        if best is None or candidate.valid_mae < best.valid_mae:
            best = candidate
    assert best is not None
    return SearchResult(best=best, candidates=rows)


def predict_change(model: Any, frame: pd.DataFrame, features: list[str] | None = None) -> np.ndarray:
    """Cambio previsto por un modelo (o por la persistencia, que siempre da 0)."""
    return np.asarray(model.predict(_matrix(frame, features or FEATURE_COLUMNS)), dtype=float)
