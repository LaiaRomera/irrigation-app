"""Error típico por horizonte para el informe (contrato C4).

Se lee de `data/ml/metrics.csv` (el LightGBM vigente, conjunto de prueba, todas las unidades). Más adelante
la misma información saldrá de `ml_model.metrics` en la base de datos (tarea 4.x de DataEngineerML); el
informe solo necesita la lista de `HorizonError`, así que el cambio de fuente no toca el resto.

Un dato que falta es `None`, nunca una cifra supuesta: si el archivo no existe, la lista queda vacía y el
informe dice que el error típico no está disponible.
"""

from __future__ import annotations

import csv
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from irrigation_app.data_layers import DATA_DIR

DEFAULT_METRICS_PATH = DATA_DIR / "ml" / "metrics.csv"


class HorizonError(BaseModel):
    """Error de la predicción de humedad a un horizonte, en puntos del índice de 0 a 100."""

    model_config = ConfigDict(frozen=True)

    horizon_days: int = Field(ge=1, le=7)
    mae: float  # error absoluto medio
    rmse: float | None = None
    n: int | None = None  # ejemplos con los que se midió
    method: str = "lightgbm"
    split: str = "test"


def load_typical_error(
    path: Path | str | None = None, *, method: str = "lightgbm", scenario: str = "all", split: str = "test"
) -> tuple[HorizonError, ...]:
    """Lee el error por horizonte de un `metrics.csv`. Sin archivo o sin filas, devuelve una tupla vacía."""
    csv_path = Path(path) if path is not None else DEFAULT_METRICS_PATH
    if not csv_path.is_file():
        return ()
    rows: dict[int, HorizonError] = {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("method") != method or row.get("scenario") != scenario or row.get("split") != split:
                continue
            horizon = int(row["horizon_days"])
            rows[horizon] = HorizonError(
                horizon_days=horizon, mae=float(row["mae"]),
                rmse=float(row["rmse"]) if row.get("rmse") else None,
                n=int(float(row["n"])) if row.get("n") else None, method=method, split=split,
            )
    return tuple(rows[h] for h in sorted(rows))


def typical_error_from_model_metrics(rows: "list[tuple[int, dict | None]]") -> tuple[HorizonError, ...]:
    """Error típico por horizonte desde `ml_model.metrics` (contrato C4), una fila `(horizonte, metrics)` por modelo activo.

    Lee `metrics["typical_error"]` (`mae`, `rmse`, `n`). Un modelo sin métricas se omite: nunca se supone una cifra.
    """
    errors: dict[int, HorizonError] = {}
    for horizon, metrics in rows:
        typical = (metrics or {}).get("typical_error") or {}
        if typical.get("mae") is None:
            continue
        errors[int(horizon)] = HorizonError(
            horizon_days=int(horizon), mae=float(typical["mae"]),
            rmse=float(typical["rmse"]) if typical.get("rmse") is not None else None,
            n=int(typical["n"]) if typical.get("n") is not None else None,
            split=str(typical.get("split", "test")),
        )
    return tuple(errors[h] for h in sorted(errors))
