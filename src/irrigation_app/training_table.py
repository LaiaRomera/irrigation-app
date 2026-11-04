"""Tabla de entrenamiento del modelo de humedad (sin meteorología todavía).

El modelo predice el **cambio** de humedad entre el día de origen y cada día de 1 a 7. El origen es
a las 05:00 UTC; como las sondas dan lecturas cada 15 minutos, la humedad «de las 05:00» es la media
de las lecturas entre las 04:45 y las 05:15. A esa hora el suelo está en reposo tras la noche y no
tiene el diente de sierra del día (la planta consume de día), así que el cambio entre dos madrugadas
mide lo ocurrido en 24 horas.
"""

from __future__ import annotations

import pandas as pd

from irrigation_app.scale import moisture_index

ORIGIN_HOUR_UTC = 5
DAWN_WINDOW_MINUTES = 15
HORIZONS = tuple(range(1, 8))

SNAPSHOT_COLUMNS = ["unit_id", "day", "depth_cm", "moisture_index", "n_readings"]
TRAINING_COLUMNS = [
    "unit_id", "depth_cm", "origin_day", "horizon_days", "base_index", "change_1d", "change_3d",
    "target_index", "target_change",
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


def build_training_table(snapshots: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    """Una fila por sonda-profundidad, día de origen y horizonte, con el cambio de humedad a predecir.

    `target_change` = humedad `horizon_days` días después menos la de origen (lo que predice el
    modelo). `change_1d` y `change_3d` son el cambio de los 1 y 3 días anteriores al origen: cómo
    se estaba moviendo el suelo. Solo hay filas si existen el día de origen y el día objetivo; los
    cambios anteriores pueden faltar (vacíos). Aún sin meteorología ni exclusión de riegos.
    """
    keys = snapshots[["unit_id", "depth_cm", "day"]]
    indexed = snapshots.set_index(["unit_id", "depth_cm", "day"])["moisture_index"]

    def index_at(offset_days: int) -> pd.Series:
        wanted = pd.MultiIndex.from_arrays([keys["unit_id"], keys["depth_cm"], keys["day"] + pd.Timedelta(days=offset_days)])
        return pd.Series(indexed.reindex(wanted).to_numpy(), index=keys.index)

    base = snapshots["moisture_index"]
    change_1d = base - index_at(-1)
    change_3d = base - index_at(-3)

    frames = []
    for horizon in horizons:
        target = index_at(horizon)
        frame = pd.DataFrame(
            {
                "unit_id": keys["unit_id"],
                "depth_cm": keys["depth_cm"],
                "origin_day": keys["day"],
                "horizon_days": horizon,
                "base_index": base,
                "change_1d": change_1d,
                "change_3d": change_3d,
                "target_index": target,
                "target_change": target - base,
            }
        )
        frames.append(frame[target.notna()])
    return pd.concat(frames, ignore_index=True)[TRAINING_COLUMNS]
