"""Persistencia con tendencia reciente: una referencia más exigente que «no cambia nada» (ronda 2).

La persistencia simple dice que mañana el suelo estará igual que hoy. Un suelo que se está secando
suele seguir secándose, así que esta referencia prolonga la tendencia de los últimos días:

    cambio previsto = k × velocidad,   con velocidad = change_1d   (cambio del último día)
                                        o velocidad = change_3d / 3 (cambio medio diario de 3 días)

`k` depende del horizonte (a más días, más recorrido) y se elige **solo con el entrenamiento**; la
variante de velocidad se elige con el MAE de **validación**. Si falta la velocidad (primer día de una
sonda o día de instalación como referencia), la previsión es 0 (persistencia simple): no se inventa
una tendencia.

**Resultado de la ronda 2 (6 de noviembre de 2026):** en los datos reales la velocidad reciente casi no
tiene relación con el cambio futuro (correlación entre −0,03 y −0,08), así que la `k` óptima es 0 y esta
referencia coincide con la persistencia simple. Por eso se añade una referencia más, declarada,
`persistence_drift`: la persistencia más un desplazamiento constante por horizonte (la mediana del
cambio en entrenamiento, que es lo que minimiza el MAE). Mide cuánto de la mejora de un modelo es
solo corregir el sesgo de «la humedad no baja».
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TREND = "persistence_trend"
DRIFT = "persistence_drift"
RATE_SOURCES = {"change_1d": 1.0, "change_3d": 3.0}  # columna -> divisor para sacar el cambio diario


def daily_rate(frame: pd.DataFrame, source: str) -> np.ndarray:
    """Velocidad de cambio diaria (puntos por día) según la variante; NaN si falta el dato."""
    return frame[source].to_numpy(dtype=float) / RATE_SOURCES[source]


def best_slope(rate: np.ndarray, actual_change: np.ndarray, max_slope: float, step: float = 0.05) -> float:
    """La `k` entre 0 y `max_slope` con menor error absoluto medio sobre las filas con velocidad conocida.

    Si no hay ninguna fila con velocidad, devuelve 0 (persistencia simple).
    """
    known = ~np.isnan(rate) & ~np.isnan(actual_change)
    if not known.any():
        return 0.0
    r, y = rate[known], actual_change[known]
    candidates = np.arange(0.0, max_slope + step / 2, step)
    errors = [np.abs(k * r - y).mean() for k in candidates]
    return float(candidates[int(np.argmin(errors))])


@dataclass
class TrendModel:
    """k × velocidad reciente; se comporta como los demás modelos (`predict`)."""

    source: str
    slope: float

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        rate = daily_rate(features, self.source)
        return np.where(np.isnan(rate), 0.0, self.slope * rate)


def fit_trend(train: pd.DataFrame, valid: pd.DataFrame, horizon_days: int, target: str = "target_change") -> tuple[TrendModel, dict]:
    """Ajusta `k` con el entrenamiento para cada variante y se queda con la de menor MAE en validación.

    El tope de `k` es 1,5 × horizonte (la extrapolación lineal exacta sería `k` = horizonte).
    Devuelve el modelo y un resumen de las dos variantes.
    """
    summary = {}
    best: TrendModel | None = None
    best_mae = np.inf
    for source in RATE_SOURCES:
        slope = best_slope(daily_rate(train, source), train[target].to_numpy(dtype=float), 1.5 * horizon_days)
        model = TrendModel(source, slope)
        mae = float(np.abs(model.predict(valid) - valid[target].to_numpy(dtype=float)).mean())
        summary[source] = {"slope": slope, "valid_mae": mae}
        if mae < best_mae:
            best, best_mae = model, mae
    assert best is not None
    summary["chosen"] = best.source
    return best, summary


@dataclass
class DriftModel:
    """Persistencia más un cambio constante (el mismo para todos los ejemplos del horizonte)."""

    offset: float

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        return np.full(len(features), self.offset)


def fit_drift(train: pd.DataFrame, target: str = "target_change") -> DriftModel:
    """Desplazamiento constante = mediana del cambio real en entrenamiento (minimiza el MAE)."""
    values = train[target].dropna().to_numpy(dtype=float)
    return DriftModel(float(np.median(values)) if values.size else 0.0)
