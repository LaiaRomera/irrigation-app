"""Reparto de la tabla de entrenamiento por ubicación y fecha de corte (tarea 3.1, decisión D3).

Tres conjuntos, iguales para todos los métodos:
- **train**: ubicaciones de entrenamiento, ejemplos cuyo día objetivo (origen + horizonte) es
  anterior o igual al corte.
- **validation**: ubicaciones de entrenamiento, ejemplos con origen a partir de corte + margen
  (7 días), para que ningún objetivo de train se solape con ellos.
- **test**: ubicaciones apartadas, mismos ejemplos posteriores a corte + margen.
Los ejemplos que no caben en ninguno (el margen, o ubicaciones apartadas antes del corte) quedan sin
asignar (`None`).

La ubicación de coordenada dudosa (`41.66_0.56`, probablemente un valor por defecto de la empresa)
no es un sitio real: sus unidades van siempre a entrenamiento y nunca se aparta.
La fecha de corte no tiene valor por defecto: la decide Laia.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

DUBIOUS_LOCATION = "41.66_0.56"
MARGIN_DAYS = 7
HOLDOUT_FRACTION = 0.2
SEED = 42
SPLIT_PATH = Path("data/ml/split.json")

TRAIN, VALIDATION, TEST = "train", "validation", "test"


@dataclass(frozen=True)
class Split:
    cutoff: str  # fecha ISO (AAAA-MM-DD): el último día objetivo de train
    margin_days: int
    seed: int
    holdout_fraction: float
    holdout_locations: list[str]
    train_locations: list[str]
    dubious_location: str = DUBIOUS_LOCATION

    @property
    def cutoff_day(self) -> pd.Timestamp:
        return pd.Timestamp(self.cutoff, tz="UTC")


def _utc_day(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True).dt.floor("D")


def _days(table: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    origin = _utc_day(table["origin_day"])
    return origin, origin + pd.to_timedelta(table["horizon_days"], unit="D")


def _locations_of(table: pd.DataFrame, unit_locations: pd.DataFrame) -> pd.Series:
    mapping = unit_locations.drop_duplicates("unit_id").set_index("unit_id")["location_id"]
    locations = table["unit_id"].map(mapping)
    if locations.isna().any():
        raise ValueError("Hay unidades de la tabla sin ubicación en unit_locations")
    return locations


def make_split(
    table: pd.DataFrame,
    unit_locations: pd.DataFrame,
    cutoff: str | pd.Timestamp,
    *,
    margin_days: int = MARGIN_DAYS,
    seed: int = SEED,
    holdout_fraction: float = HOLDOUT_FRACTION,
    dubious_location: str = DUBIOUS_LOCATION,
) -> Split:
    """Elige con una semilla fija las ubicaciones que se apartan para test.

    Candidatas: ubicaciones reales (no la dudosa) con datos a los dos lados del corte (ejemplos de
    train y de test posibles). Se aparta `holdout_fraction` de las ubicaciones reales (redondeado,
    al menos una), sin pasar del número de candidatas.
    """
    cutoff_day = pd.Timestamp(cutoff)
    cutoff_day = cutoff_day.tz_localize("UTC") if cutoff_day.tzinfo is None else cutoff_day.tz_convert("UTC")
    origin, target = _days(table)
    locations = _locations_of(table, unit_locations)

    before = set(locations[target <= cutoff_day])
    after = set(locations[origin >= cutoff_day + pd.Timedelta(days=margin_days)])
    all_locations = sorted(set(locations))
    real = [loc for loc in all_locations if loc != dubious_location]
    candidates = sorted((before & after) - {dubious_location})

    n_holdout = min(len(candidates), max(1, round(holdout_fraction * len(real)))) if candidates else 0
    chosen = set()
    if n_holdout:
        chosen = set(np.random.default_rng(seed).choice(candidates, size=n_holdout, replace=False).tolist())
    return Split(
        cutoff=cutoff_day.strftime("%Y-%m-%d"),
        margin_days=margin_days,
        seed=seed,
        holdout_fraction=holdout_fraction,
        holdout_locations=sorted(str(loc) for loc in chosen),
        train_locations=[loc for loc in all_locations if loc not in chosen],
        dubious_location=dubious_location,
    )


def assign_split(table: pd.DataFrame, unit_locations: pd.DataFrame, split: Split) -> pd.Series:
    """Etiqueta cada fila con 'train', 'validation', 'test' o None (margen / sin conjunto).

    Sirve para cualquier tabla con `unit_id`, `origin_day` y `horizon_days`; usa solo el reparto
    guardado, así todos los métodos ven los mismos ejemplos.
    """
    origin, target = _days(table)
    locations = _locations_of(table, unit_locations)
    held_out = locations.isin(split.holdout_locations) & (locations != split.dubious_location)
    validation_start = split.cutoff_day + pd.Timedelta(days=split.margin_days)

    labels = pd.Series(None, index=table.index, dtype="object")
    labels[~held_out & (target <= split.cutoff_day)] = TRAIN
    labels[~held_out & (origin >= validation_start)] = VALIDATION
    labels[held_out & (origin >= validation_start)] = TEST
    return labels


def save_split(split: Split, path: Path | str = SPLIT_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(split), indent=2, ensure_ascii=False), encoding="utf-8")


def load_split(path: Path | str = SPLIT_PATH) -> Split:
    return Split(**json.loads(Path(path).read_text(encoding="utf-8")))
