"""Fuente Agrodevices: construye la tabla de unidades (sondas) a partir de sus exportaciones.

Cruza la tabla de dispositivos (`player_data`: modelo, coordenadas, activo) con la
configuración de las sondas (`profiler_setup`: profundidades y rango de la escala).
No lee las lecturas de humedad; solo necesita saber qué unidades tienen lecturas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

DEVICE_MODEL = "SMM"
# Unidades de demostración de la empresa, sin datos reales (indicado por Laia, 3-11-2026).
DEMO_UNIT_IDS = frozenset({2000, 2001})
N_CHANNELS = 5
# Coordenada que la plataforma asigna por defecto a los proyectos sin ubicación («Project N»).
PLACEHOLDER_COORDINATES = (44.5190842342537500, 40.1956274998365100)
# Decimales con los que se comparan coordenadas para detectar ubicaciones compartidas (~100 m).
COORDINATE_ROUNDING = 3

# Tipos de lectura que no se guardan: `SMM0` tiene todos los canales a 0 (sin datos).
# `SMMi` (interpolada) se conserva marcada y `SMMAdmin` (significado sin confirmar) también.
EXCLUDED_READING_TYPES = frozenset({"SMM0"})
READING_COLUMNS = ["unit_id", "timestamp", "depth_cm", "frequency", "reading_type"]

DEPTH_COLUMNS = [f"depth_cm_{i}" for i in range(1, N_CHANNELS + 1)]
AVERAGE_COLUMNS = [f"in_average_{i}" for i in range(1, N_CHANNELS + 1)]
UNIT_COLUMNS = [
    "unit_id", "name", "project_id", "client_id", "latitude", "longitude", "coord_status",
    "smm_min", "smm_max", *DEPTH_COLUMNS, *AVERAGE_COLUMNS, "n_depths",
]


@dataclass(frozen=True)
class FilterStep:
    """Un filtro aplicado a las unidades y las que quitó."""

    name: str
    units_before: int
    units_after: int
    removed_ids: tuple[int, ...]


def read_devices(path: Path | str) -> pd.DataFrame:
    """Lee `player_data` con el identificador ya renombrado a `unit_id`."""
    raw = pd.read_csv(path, dtype={"UserName": "string"})
    name = raw["UserName"].str.strip()
    devices = pd.DataFrame(
        {
            "unit_id": raw["Device_ID"],
            "model": raw["Model"],
            "name": name.mask(name == ""),
            "project_id": raw["ProjectID"],
            "client_id": raw["ClientID"],
            "latitude": raw["Latitud"],
            "longitude": raw["Longitud"],
            "active": raw["Activo"],
        }
    )
    # El identificador 0 son filas sin registrar, no dispositivos.
    return devices[devices["unit_id"] != 0].reset_index(drop=True)


def _parse_depths(levels: str) -> list[int | None]:
    """'015-025-035-000-055-...' -> [15, 25, 35, None, 55]; solo los 5 primeros, 0 = sin sensor."""
    values = [int(v) for v in levels.split("-")][:N_CHANNELS]
    return [v if v > 0 else None for v in values]


def _parse_average_flags(flags: str) -> list[bool]:
    """'1,1,1,1,0' -> qué canales entran en la media de la sonda."""
    return [v == "1" for v in flags.split(",")][:N_CHANNELS]


def read_profiles(path: Path | str) -> pd.DataFrame:
    """Lee `profiler_setup`: una fila por unidad, con la configuración más reciente."""
    raw = pd.read_csv(path)
    # Alguna unidad aparece dos veces: se queda la fila más reciente (mayor id).
    raw = raw.sort_values("id").drop_duplicates("UnitID", keep="last")
    rows = []
    for r in raw.itertuples(index=False):
        row = {"unit_id": r.UnitID, "smm_min": r.SMMMin, "smm_max": r.SMMMax}
        row.update(zip(DEPTH_COLUMNS, _parse_depths(r.Levels)))
        row.update(zip(AVERAGE_COLUMNS, _parse_average_flags(r.AverageFreqLevels)))
        rows.append(row)
    profiles = pd.DataFrame(rows)
    profiles[DEPTH_COLUMNS] = profiles[DEPTH_COLUMNS].astype("Int64")
    return profiles.reset_index(drop=True)


def read_reading_unit_ids(path: Path | str) -> set[int]:
    """Unidades que aparecen en el CSV de lecturas (solo lee la columna UnitID)."""
    return set(pd.read_csv(path, usecols=["UnitID"])["UnitID"].unique())


def _filter(steps: list[FilterStep], name: str, units: pd.DataFrame, keep: pd.Series) -> pd.DataFrame:
    """Aplica un filtro y anota cuántas unidades quita."""
    kept = units[keep]
    removed = sorted(set(units["unit_id"]) - set(kept["unit_id"]))
    steps.append(FilterStep(name, len(units), len(kept), tuple(int(u) for u in removed)))
    return kept


def build_units(
    devices: pd.DataFrame,
    profiles: pd.DataFrame,
    reading_unit_ids: set[int] | None = None,
) -> tuple[pd.DataFrame, list[FilterStep]]:
    """Devuelve la tabla de unidades utilizables y el registro de lo que se quitó en cada filtro."""
    steps: list[FilterStep] = []
    units = devices

    units = _filter(steps, f"modelo {DEVICE_MODEL}", units, units["model"] == DEVICE_MODEL)
    units = _filter(steps, "activa (Activo = 1)", units, units["active"] == 1)
    units = _filter(steps, "no es una unidad de demostración", units, ~units["unit_id"].isin(DEMO_UNIT_IDS))
    if reading_unit_ids is not None:
        units = _filter(steps, "tiene lecturas", units, units["unit_id"].isin(reading_unit_ids))
    units = _filter(steps, "tiene configuración de sonda", units, units["unit_id"].isin(profiles["unit_id"]))

    units = units.merge(profiles, on="unit_id", how="left")
    units["n_depths"] = units[DEPTH_COLUMNS].notna().sum(axis=1)
    units = _filter(steps, "tiene alguna profundidad", units, units["n_depths"] > 0)

    no_location = (units["latitude"] == 0) & (units["longitude"] == 0)
    placeholder = np.isclose(units["latitude"], PLACEHOLDER_COORDINATES[0]) & np.isclose(
        units["longitude"], PLACEHOLDER_COORDINATES[1]
    )
    units = _filter(steps, "tiene coordenadas reales", units, ~(no_location | placeholder))

    # Una misma ubicación usada por clientes distintos puede ser la misma finca dada de alta dos
    # veces o una coordenada por defecto de la empresa: se marca como compartida y se confirma aparte.
    place = units["latitude"].round(COORDINATE_ROUNDING).astype(str) + "," + units["longitude"].round(
        COORDINATE_ROUNDING
    ).astype(str)
    clients_in_place = units.groupby(place)["client_id"].transform("nunique")
    units = units.assign(coord_status=np.where(clients_in_place > 1, "shared", "ok"))

    return units[UNIT_COLUMNS].sort_values("unit_id").reset_index(drop=True), steps


@dataclass
class ReadingsReport:
    """Qué ha pasado con las filas del CSV de lecturas al traducirlo."""

    rows_read: int = 0
    rows_other_units: int = 0
    rows_excluded_by_type: dict[str, int] = field(default_factory=dict)
    rows_kept: int = 0
    values_kept: int = 0
    # Canales a 0 (sin dato) o sin profundidad configurada: no se guardan.
    values_discarded: int = 0


def build_readings(raw: pd.DataFrame, units: pd.DataFrame, report: ReadingsReport) -> pd.DataFrame:
    """Pasa un trozo de lecturas (una fila por instante) a formato largo (una por profundidad)."""
    report.rows_read += len(raw)
    in_table = raw["UnitID"].isin(units["unit_id"])
    report.rows_other_units += int((~in_table).sum())
    raw = raw[in_table]

    excluded = raw["Type"].isin(EXCLUDED_READING_TYPES)
    for reading_type, count in raw.loc[excluded, "Type"].value_counts().items():
        report.rows_excluded_by_type[reading_type] = report.rows_excluded_by_type.get(reading_type, 0) + int(count)
    raw = raw[~excluded]
    report.rows_kept += len(raw)

    # La fecha del CSV es UTC (pendiente de confirmar con Agrodevices).
    base = pd.DataFrame(
        {
            "unit_id": raw["UnitID"].to_numpy(),
            "timestamp": pd.to_datetime(raw["fecha"], utc=True).array,
            "reading_type": raw["Type"].to_numpy(),
        }
    )
    depths = units.set_index("unit_id")[DEPTH_COLUMNS]
    pieces = []
    for channel in range(1, N_CHANNELS + 1):
        frequency = raw[f"Frec_{channel}"].to_numpy()
        depth = depths[f"depth_cm_{channel}"].reindex(base["unit_id"]).to_numpy()
        # 0 en un canal = sin dato; un canal sin profundidad no se puede interpretar.
        valid = (frequency > 0) & ~pd.isna(depth)
        report.values_kept += int(valid.sum())
        report.values_discarded += int((~valid).sum())
        piece = base[valid].copy()
        piece["depth_cm"] = depth[valid].astype("uint16")
        piece["frequency"] = frequency[valid].astype("uint16")
        pieces.append(piece)
    return pd.concat(pieces, ignore_index=True)[READING_COLUMNS]


def read_readings(
    path: Path | str, units: pd.DataFrame, chunk_rows: int = 500_000
) -> tuple[pd.DataFrame, ReadingsReport]:
    """Lee el CSV de lecturas por trozos (pesa cientos de MB) y devuelve la tabla larga ordenada."""
    report = ReadingsReport()
    columns = ["UnitID", "fecha", "Type", *(f"Frec_{i}" for i in range(1, N_CHANNELS + 1))]
    chunks = [
        build_readings(chunk, units, report)
        for chunk in pd.read_csv(path, usecols=columns, chunksize=chunk_rows)
    ]
    readings = pd.concat(chunks, ignore_index=True)
    readings = readings.sort_values(["unit_id", "timestamp", "depth_cm"]).reset_index(drop=True)
    return readings, report
