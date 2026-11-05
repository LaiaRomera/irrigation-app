"""Tabla de entrenamiento del modelo de humedad (sin meteorología todavía).

El modelo predice el **cambio** de humedad entre el día de origen y cada día de 1 a 7. El origen es
a las 05:00 UTC; como las sondas dan lecturas cada 15 minutos, la humedad «de las 05:00» es la media
de las lecturas entre las 04:45 y las 05:15. A esa hora el suelo está en reposo tras la noche y no
tiene el diente de sierra del día (la planta consume de día), así que el cambio entre dos madrugadas
mide lo ocurrido en 24 horas.

Exclusiones (tarea 2.3), antes de entrenar:
- **Días de instalación** (sonda sin enterrar): un ejemplo se quita si el día de origen o el día
  objetivo es de instalación. Si solo el día de referencia de `change_1d` / `change_3d` lo es,
  el ejemplo se conserva y ese campo queda nulo (el objetivo a predecir es normal).
- **Riego marcado**, por horizonte: un ejemplo de horizonte N se quita si hay un riego marcado en
  los días origen+1 a origen+N (el modelo predice «sin riego futuro»). Un riego anterior al origen
  o del propio día de origen no se excluye: ya está reflejado en la humedad de partida.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from irrigation_app.irrigation_marks import installation_mask
from irrigation_app.scale import moisture_index

ORIGIN_HOUR_UTC = 5
DAWN_WINDOW_MINUTES = 15
HORIZONS = tuple(range(1, 8))
DAYS_PER_YEAR = 365.25

SNAPSHOT_COLUMNS = ["unit_id", "day", "depth_cm", "moisture_index", "n_readings"]
TRAINING_COLUMNS = [
    "unit_id", "depth_cm", "origin_day", "horizon_days", "base_index", "change_1d", "change_3d",
    "doy_sin", "doy_cos", "target_index", "target_change",
]
COUNT_COLUMNS = [
    "initial", "removed_installation", "removed_irrigation", "final", "change_1d_nulled", "change_3d_nulled",
]


def dawn_snapshots(
    readings: pd.DataFrame,
    units: pd.DataFrame,
    origin_hour: int = ORIGIN_HOUR_UTC,
    window_minutes: int = DAWN_WINDOW_MINUTES,
) -> pd.DataFrame:
    """Una fila por unidad, día y profundidad: el índice de humedad de la hora de origen.

    Es la media de la frecuencia de las lecturas a menos de `window_minutes` de la hora de origen,
    pasada a la escala 0 a 100 de la sonda. Un día sin ninguna lectura en esa ventana no aparece.
    """
    timestamp = readings["timestamp"]
    distance = (timestamp.dt.hour * 60 + timestamp.dt.minute - origin_hour * 60).abs()
    near = readings[distance <= window_minutes]
    snapshots = (
        near.assign(day=near["timestamp"].dt.floor("D"))
        .groupby(["unit_id", "day", "depth_cm"], as_index=False)
        .agg(frequency=("frequency", "mean"), n_readings=("frequency", "size"))
    )
    ranges = units.set_index("unit_id")[["smm_min", "smm_max"]].reindex(snapshots["unit_id"])
    smm_min = pd.Series(ranges["smm_min"].to_numpy(dtype=float), index=snapshots.index)
    smm_max = pd.Series(ranges["smm_max"].to_numpy(dtype=float), index=snapshots.index)
    snapshots["moisture_index"] = moisture_index(snapshots["frequency"], smm_min, smm_max)
    return snapshots[SNAPSHOT_COLUMNS]


def seasonal_features(days: pd.Series) -> pd.DataFrame:
    """Seno y coseno del día del año de `days` (fechas): la estación como dos números continuos.

    Se usa un círculo de 365,25 días, así el 31 de diciembre y el 1 de enero quedan contiguos. Es
    la misma función para entrenar y para predecir.
    """
    angle = 2 * np.pi * days.dt.dayofyear.to_numpy() / DAYS_PER_YEAR
    return pd.DataFrame({"doy_sin": np.sin(angle), "doy_cos": np.cos(angle)}, index=days.index)


def _empty_days() -> pd.DataFrame:
    return pd.DataFrame({"unit_id": pd.Series(dtype="int64"), "day": pd.Series(dtype="datetime64[ns, UTC]")})


def _marked_irrigation_days(irrigation: pd.DataFrame | None) -> pd.DataFrame:
    """Pares (`unit_id`, `day`) con riego marcado, a partir de la salida de `possible_irrigation`."""
    if irrigation is None or irrigation.empty:
        return _empty_days()
    return irrigation.loc[irrigation["possible_irrigation"], ["unit_id", "day"]]


def build_training_table_with_counts(
    snapshots: pd.DataFrame,
    horizons: tuple[int, ...] = HORIZONS,
    installation: pd.DataFrame | None = None,
    irrigation: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Como `build_training_table`, y además el recuento de ejemplos por horizonte.

    `installation`: pares (`unit_id`, `day`) de instalación (`irrigation_marks.installation_days`).
    `irrigation`: salida de `irrigation_marks.possible_irrigation` (columnas `unit_id`, `day` y
    `possible_irrigation`). Si es None no se excluyen riegos. El recuento (índice `horizon_days`)
    trae `initial` (con día de origen y objetivo), `removed_installation`, `removed_irrigation`
    (sobre los que quedan tras quitar instalación), `final`, y cuántos `change_1d` y `change_3d`
    de los ejemplos finales quedaron nulos por apoyarse en un día de instalación.
    """
    if installation is None:
        installation = _empty_days()
    marked = pd.MultiIndex.from_frame(_marked_irrigation_days(irrigation)[["unit_id", "day"]])

    keys = snapshots[["unit_id", "depth_cm", "day"]]
    indexed = snapshots.set_index(["unit_id", "depth_cm", "day"])["moisture_index"]

    def shifted_days(offset_days: int) -> pd.Series:
        return keys["day"] + pd.Timedelta(days=offset_days)

    def index_at(offset_days: int) -> pd.Series:
        wanted = pd.MultiIndex.from_arrays([keys["unit_id"], keys["depth_cm"], shifted_days(offset_days)])
        return pd.Series(indexed.reindex(wanted).to_numpy(), index=keys.index)

    def installation_at(offset_days: int) -> pd.Series:
        return installation_mask(keys.assign(day=shifted_days(offset_days)), installation)

    def irrigation_at(offset_days: int) -> pd.Series:
        wanted = pd.MultiIndex.from_arrays([keys["unit_id"], shifted_days(offset_days)])
        return pd.Series(wanted.isin(marked), index=keys.index)

    base = snapshots["moisture_index"]
    change_1d_raw = base - index_at(-1)
    change_3d_raw = base - index_at(-3)
    nulled_1d = installation_at(-1) & change_1d_raw.notna()
    nulled_3d = installation_at(-3) & change_3d_raw.notna()
    change_1d = change_1d_raw.mask(nulled_1d)
    change_3d = change_3d_raw.mask(nulled_3d)
    season = seasonal_features(keys["day"])
    origin_installation = installation_at(0)

    frames = []
    counts = {}
    irrigation_ahead = pd.Series(False, index=keys.index)  # riego marcado en origen+1 .. origen+N
    for step in range(1, max(horizons) + 1):
        irrigation_ahead = irrigation_ahead | irrigation_at(step)
        if step not in horizons:
            continue
        target = index_at(step)
        present = target.notna()
        by_installation = present & (origin_installation | installation_at(step))
        by_irrigation = present & ~by_installation & irrigation_ahead
        keep = present & ~by_installation & ~by_irrigation
        frame = pd.DataFrame(
            {
                "unit_id": keys["unit_id"],
                "depth_cm": keys["depth_cm"],
                "origin_day": keys["day"],
                "horizon_days": step,
                "base_index": base,
                "change_1d": change_1d,
                "change_3d": change_3d,
                "doy_sin": season["doy_sin"],
                "doy_cos": season["doy_cos"],
                "target_index": target,
                "target_change": target - base,
            }
        )
        frames.append(frame[keep])
        counts[step] = (
            int(present.sum()), int(by_installation.sum()), int(by_irrigation.sum()), int(keep.sum()),
            int((keep & nulled_1d).sum()), int((keep & nulled_3d).sum()),
        )
    table = pd.concat(frames, ignore_index=True)[TRAINING_COLUMNS]
    count_table = pd.DataFrame.from_dict(counts, orient="index", columns=COUNT_COLUMNS)
    count_table.index.name = "horizon_days"
    return table, count_table


def build_training_table(
    snapshots: pd.DataFrame,
    horizons: tuple[int, ...] = HORIZONS,
    installation: pd.DataFrame | None = None,
    irrigation: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Una fila por sonda-profundidad, día de origen y horizonte, con el cambio de humedad a predecir.

    `target_change` = humedad `horizon_days` días después menos la de origen (lo que predice el
    modelo). `change_1d` y `change_3d` son el cambio de los 1 y 3 días anteriores al origen: cómo
    se estaba moviendo el suelo; pueden faltar (vacíos), y también quedan vacíos si el día de
    referencia es de instalación. `doy_sin` y `doy_cos` son la estación del día de origen. Solo hay
    filas si existen el día de origen y el día objetivo. Con `installation` e `irrigation` se
    aplican las exclusiones del encabezado del módulo (sin ellos, no se excluye nada). Aún sin
    meteorología.
    """
    return build_training_table_with_counts(snapshots, horizons, installation, irrigation)[0]
