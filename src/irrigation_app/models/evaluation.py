"""Métricas de los modelos de humedad (tarea 3.3).

Para cada método y horizonte, sobre **los mismos ejemplos**:
- MAE: error absoluto medio, en puntos del índice de humedad.
- RMSE: raíz del error cuadrático medio (castiga más los fallos grandes).
- Sesgo: media de (previsto − real). Positivo = el método suele prever más humedad de la real.
- Mejora frente a la persistencia: (error de la persistencia − error del método) / error de la
  persistencia, en %. Positiva = mejor que «no cambia nada»; negativa = peor.

Como nivel = origen + cambio, el error del nivel y el del cambio son el mismo número, así que se
calcula sobre el cambio.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

BASELINE = "persistence"
METRIC_COLUMNS = ["method", "horizon_days", "n", "mae", "rmse", "bias", "mae_improvement_pct", "rmse_improvement_pct"]


def error_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    """MAE, RMSE y sesgo (previsto − real) de dos series del mismo tamaño."""
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    if actual.shape != predicted.shape:
        raise ValueError("Real y previsto deben tener el mismo tamaño")
    if actual.size == 0:
        return {"n": 0, "mae": np.nan, "rmse": np.nan, "bias": np.nan}
    error = predicted - actual
    return {
        "n": int(actual.size),
        "mae": float(np.abs(error).mean()),
        "rmse": float(np.sqrt((error**2).mean())),
        "bias": float(error.mean()),
    }


def improvement_pct(baseline_error: float, error: float) -> float:
    """Mejora relativa en % respecto al error de la persistencia (positiva = mejor)."""
    if not baseline_error or np.isnan(baseline_error):
        return float("nan")
    return float((baseline_error - error) / baseline_error * 100)


def evaluate_methods(
    horizon_days: int,
    actual_change: np.ndarray,
    predicted_changes: Mapping[str, np.ndarray],
) -> pd.DataFrame:
    """Métricas de varios métodos en un horizonte, con la mejora frente a la persistencia.

    `predicted_changes` debe incluir `persistence`. Todos los métodos se evalúan sobre las mismas
    filas de `actual_change`.
    """
    if BASELINE not in predicted_changes:
        raise ValueError("Falta la persistencia, que es la referencia")
    rows = []
    for method, predicted in predicted_changes.items():
        rows.append({"method": method, "horizon_days": int(horizon_days), **error_metrics(actual_change, predicted)})
    frame = pd.DataFrame(rows)
    base = frame[frame["method"] == BASELINE].iloc[0]
    frame["mae_improvement_pct"] = [improvement_pct(base["mae"], v) for v in frame["mae"]]
    frame["rmse_improvement_pct"] = [improvement_pct(base["rmse"], v) for v in frame["rmse"]]
    return frame[METRIC_COLUMNS]
