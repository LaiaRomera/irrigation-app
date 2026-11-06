"""Registro de los modelos entrenados en `ml_model` (tarea 4.3, contrato C4).

Una fila por algoritmo y horizonte (1 a 7). Solo un modelo activo por horizonte (lo garantiza un
índice único parcial); activar uno desactiva el anterior en la misma transacción. Los archivos de los
modelos (`.joblib`) quedan fuera de la base: `artifact_path` es la ruta relativa a la raíz del proyecto.

`metrics` (contrato C4) guarda, por horizonte, el MAE, el RMSE y el sesgo del backtesting junto con los
de la persistencia, con todas las unidades (`all`) y sin las de coordenada compartida (`no_shared`):

    {"unit": "index_points", "source": "data/ml/metrics.csv",
     "typical_error": {"split": "test", "scenario": "all", "n": ..., "mae": ..., "rmse": ..., "bias": ...},
     "scenarios": {"all": {"validation": {...}, "test": {...}}, "no_shared": {...}}}

y cada bloque `{n, mae, rmse, bias, mae_improvement_pct, rmse_improvement_pct, persistence: {mae, rmse, bias}}`.
De `typical_error` sale el «error típico» que muestra el informe.

Los modelos se cargan con joblib, que usa pickle: solo se abren archivos dentro de `data/` del proyecto.
"""

from __future__ import annotations

import datetime as dt
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import joblib
import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from irrigation_app.data_layers import DATA_DIR, PROJECT_ROOT
from irrigation_app.models.training import FEATURE_COLUMNS, LIGHTGBM, PERSISTENCE, TARGET

DATA_SOURCE = "agrodevices"
MOISTURE_UNIT = "relative_index_0_100"  # índice relativo de la sonda (0 seco, 100 húmedo), no humedad volumétrica
METRICS_SCENARIOS = ("all", "no_shared")
METRICS_SPLITS = ("validation", "test")
TYPICAL_ERROR_SCENARIO, TYPICAL_ERROR_SPLIT = "all", "test"
HORIZONS = tuple(range(1, 8))


@dataclass(frozen=True)
class ModelRecord:
    """Los datos de una fila de `ml_model` (sin `model_id` ni `is_active`)."""

    algorithm: str
    horizon_days: int
    trained_at: dt.datetime
    data_source: str
    moisture_unit: str
    features: dict[str, Any]
    train_from: dt.date | None = None
    train_to: dt.date | None = None
    hyperparameters: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    artifact_path: str | None = None
    code_version: str | None = None


@dataclass(frozen=True)
class LoadedModel:
    """Un modelo listo para predecir. `model_id` es None si se cargó de una carpeta, sin base de datos."""

    algorithm: str
    horizon_days: int
    model: Any
    features: list[str]
    model_id: int | None = None
    training_depths: tuple[int, ...] | None = None  # profundidades vistas al entrenar; None = no se sabe


# --- Construcción de los registros a partir de los archivos del entrenamiento -----------------------

def _clean(value: Any) -> Any:
    """Número o None: los NaN no caben en JSON."""
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) else value


def build_metrics(metrics: pd.DataFrame, algorithm: str, horizon_days: int) -> dict[str, Any]:
    """Estructura `ml_model.metrics` de un algoritmo y horizonte a partir de `metrics.csv` (ver el encabezado)."""
    scenarios: dict[str, dict[str, Any]] = {}
    for scenario in METRICS_SCENARIOS:
        for split_name in METRICS_SPLITS:
            rows = metrics[
                (metrics["scenario"] == scenario) & (metrics["split"] == split_name) & (metrics["horizon_days"] == horizon_days)
            ]
            own = rows[rows["method"] == algorithm]
            baseline = rows[rows["method"] == PERSISTENCE]
            if own.empty or baseline.empty:
                continue
            own_row, base_row = own.iloc[0], baseline.iloc[0]
            scenarios.setdefault(scenario, {})[split_name] = {
                "n": int(own_row["n"]),
                "mae": _clean(own_row["mae"]),
                "rmse": _clean(own_row["rmse"]),
                "bias": _clean(own_row["bias"]),
                "mae_improvement_pct": _clean(own_row["mae_improvement_pct"]),
                "rmse_improvement_pct": _clean(own_row["rmse_improvement_pct"]),
                "persistence": {k: _clean(base_row[k]) for k in ("mae", "rmse", "bias")},
            }
    typical = scenarios.get(TYPICAL_ERROR_SCENARIO, {}).get(TYPICAL_ERROR_SPLIT)
    return {
        "unit": "index_points",
        "source": "data/ml/metrics.csv",
        "typical_error": (
            {"split": TYPICAL_ERROR_SPLIT, "scenario": TYPICAL_ERROR_SCENARIO, **{k: typical[k] for k in ("n", "mae", "rmse", "bias")}}
            if typical
            else None
        ),
        "scenarios": scenarios,
    }


def current_code_version() -> str | None:
    """`git:<commit>` del código al registrar (con `-dirty` si hay cambios sin confirmar); None si no hay git."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return f"git:{commit}{'-dirty' if dirty else ''}"


def training_date_ranges(table: pd.DataFrame, labels: pd.Series, training_label: str = "train") -> dict[int, tuple[dt.date, dt.date]]:
    """Primer y último día de origen de los ejemplos de entrenamiento, por horizonte."""
    train = table.loc[labels.to_numpy() == training_label, ["horizon_days", "origin_day"]]
    ranges = {}
    for horizon, group in train.groupby("horizon_days"):
        days = pd.to_datetime(group["origin_day"], utc=True)
        ranges[int(horizon)] = (days.min().date(), days.max().date())
    return ranges


def build_records(
    summary: Mapping[str, Any],
    metrics: pd.DataFrame,
    *,
    algorithms: Iterable[str] = (LIGHTGBM,),
    models_dir: Path | None = None,
    train_ranges: Mapping[int, tuple[dt.date, dt.date]] | None = None,
    code_version: str | None = None,
    training_depths: Iterable[int] | None = None,
) -> list[ModelRecord]:
    """Un `ModelRecord` por algoritmo y horizonte, con los datos de `summary.json` y `metrics.csv`.

    `models_dir` es la carpeta de los `.joblib` (por defecto `data/ml/models`); la ruta se guarda
    relativa a la raíz del proyecto. La persistencia no tiene archivo.
    """
    models_dir = models_dir or DATA_DIR / "ml" / "models"
    trained_at = dt.datetime.fromisoformat(str(summary["generated_at"]).replace("Z", "+00:00"))
    chosen = summary["chosen"]["all"]
    versions = summary.get("versions", {})
    records = []
    for algorithm in algorithms:
        for horizon in HORIZONS:
            params = chosen.get(algorithm, {}).get(str(horizon)) if algorithm != PERSISTENCE else {"params": {}}
            if params is None:
                raise ValueError(f"summary.json no tiene el modelo {algorithm} del horizonte {horizon}")
            artifact = None
            if algorithm != PERSISTENCE:
                path = models_dir / f"{algorithm}_h{horizon}.joblib"
                resolved = path.resolve()
                artifact = resolved.relative_to(PROJECT_ROOT).as_posix() if resolved.is_relative_to(PROJECT_ROOT) else resolved.as_posix()
            first_day, last_day = (train_ranges or {}).get(horizon, (None, None))
            records.append(
                ModelRecord(
                    algorithm=algorithm,
                    horizon_days=horizon,
                    trained_at=trained_at,
                    train_from=first_day,
                    train_to=last_day,
                    data_source=DATA_SOURCE,
                    moisture_unit=MOISTURE_UNIT,
                    features={"columns": list(FEATURE_COLUMNS) if algorithm != PERSISTENCE else [], "target": TARGET, "versions": versions,
                              **({"training_depths_cm": sorted(int(d) for d in training_depths)} if training_depths is not None else {})},
                    hyperparameters=(
                        {"params": params["params"], "best_iteration": params.get("best_iteration"), "valid_mae": _clean(params.get("valid_mae"))}
                        if algorithm != PERSISTENCE
                        else {}
                    ),
                    metrics=build_metrics(metrics, algorithm, horizon),
                    artifact_path=artifact,
                    code_version=code_version,
                )
            )
    return records


# --- Base de datos -----------------------------------------------------------------------------------

def register_model(connection: psycopg.Connection, record: ModelRecord, *, activate: bool = False) -> int:
    """Inserta el modelo en `ml_model` y devuelve su `model_id`; con `activate` lo deja como el activo del horizonte.

    Es idempotente: si ya hay una fila con el mismo algoritmo, horizonte, fecha de entrenamiento y
    archivo, no se duplica y se devuelve la existente.
    """
    with connection.transaction():
        row = connection.execute(
            "SELECT model_id FROM ml_model WHERE algorithm = %s AND horizon_days = %s AND trained_at = %s "
            "AND artifact_path IS NOT DISTINCT FROM %s",
            (record.algorithm, record.horizon_days, record.trained_at, record.artifact_path),
        ).fetchone()
        if row:
            model_id = int(row[0])
        else:
            model_id = int(
                connection.execute(
                    "INSERT INTO ml_model (algorithm, horizon_days, trained_at, train_from, train_to, data_source, "
                    "moisture_unit, features, hyperparameters, metrics, artifact_path, code_version) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING model_id",
                    (
                        record.algorithm, record.horizon_days, record.trained_at, record.train_from, record.train_to,
                        record.data_source, record.moisture_unit, Jsonb(record.features),
                        Jsonb(record.hyperparameters) if record.hyperparameters is not None else None,
                        Jsonb(record.metrics) if record.metrics is not None else None,
                        record.artifact_path, record.code_version,
                    ),
                ).fetchone()[0]
            )
        if activate:
            activate_model(connection, model_id)
    return model_id


def activate_model(connection: psycopg.Connection, model_id: int) -> None:
    """Deja `model_id` como el modelo activo de su horizonte y desactiva el anterior (misma transacción)."""
    with connection.transaction():
        row = connection.execute("SELECT horizon_days FROM ml_model WHERE model_id = %s", (model_id,)).fetchone()
        if row is None:
            raise ValueError(f"No existe el modelo {model_id}")
        connection.execute(
            "UPDATE ml_model SET is_active = FALSE WHERE horizon_days = %s AND is_active AND model_id <> %s",
            (row[0], model_id),
        )
        connection.execute("UPDATE ml_model SET is_active = TRUE WHERE model_id = %s", (model_id,))


def active_models(connection: psycopg.Connection) -> dict[int, dict[str, Any]]:
    """Filas activas de `ml_model` por horizonte (`model_id`, `algorithm`, `features`, `artifact_path`, `metrics`)."""
    rows = connection.execute(
        "SELECT horizon_days, model_id, algorithm, features, artifact_path, metrics FROM ml_model WHERE is_active ORDER BY horizon_days"
    ).fetchall()
    return {
        int(h): {"model_id": int(mid), "algorithm": alg, "features": feats, "artifact_path": path, "metrics": met}
        for h, mid, alg, feats, path, met in rows
    }


def resolve_artifact(artifact_path: str) -> Path:
    """Ruta absoluta del archivo de un modelo; solo se aceptan archivos dentro de `data/` del proyecto."""
    path = (PROJECT_ROOT / artifact_path).resolve()
    if not path.is_relative_to(DATA_DIR.resolve()):
        raise ValueError("El archivo del modelo debe estar dentro de la carpeta data/ del proyecto")
    if not path.exists():
        raise FileNotFoundError(f"No se encuentra el archivo del modelo {artifact_path}")
    return path


def _depths(features: Mapping[str, Any] | None) -> tuple[int, ...] | None:
    depths = (features or {}).get("training_depths_cm")
    return None if depths is None else tuple(int(d) for d in depths)


def load_active_models(connection: psycopg.Connection) -> dict[int, LoadedModel]:
    """Carga los modelos activos (con su `model_id`) para predecir. Un horizonte sin modelo activo no aparece."""
    loaded = {}
    for horizon, row in active_models(connection).items():
        if row["algorithm"] == PERSISTENCE or not row["artifact_path"]:
            continue
        columns = (row["features"] or {}).get("columns") or list(FEATURE_COLUMNS)
        loaded[horizon] = LoadedModel(
            algorithm=row["algorithm"],
            horizon_days=horizon,
            model=joblib.load(resolve_artifact(row["artifact_path"])),
            features=list(columns),
            model_id=row["model_id"],
            training_depths=_depths(row["features"]),
        )
    return loaded


def load_models_from_directory(
    models_dir: Path, algorithm: str = LIGHTGBM, training_depths: Iterable[int] | None = None
) -> dict[int, LoadedModel]:
    """Carga `<algoritmo>_h<N>.joblib` de una carpeta, sin base de datos (`model_id` queda vacío)."""
    loaded = {}
    for horizon in HORIZONS:
        path = Path(models_dir) / f"{algorithm}_h{horizon}.joblib"
        if path.exists():
            loaded[horizon] = LoadedModel(
                algorithm, horizon, joblib.load(path), list(FEATURE_COLUMNS),
                training_depths=None if training_depths is None else tuple(int(d) for d in training_depths),
            )
    return loaded
