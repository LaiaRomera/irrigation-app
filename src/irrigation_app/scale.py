"""Escala común de humedad entre sondas.

La frecuencia en bruto no es comparable entre unidades: cada sonda tiene su rango de
calibración (`smm_min`, `smm_max`). Se reescala linealmente a un índice de 0 a 100 por unidad.
No es humedad volumétrica ni hay calibración de suelo: es un índice relativo al rango de la sonda,
y la linealidad es una suposición declarada.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SCALE_COLUMNS = ["moisture_index_mean", "moisture_index_min", "moisture_index_max"]


def moisture_index(frequency: pd.Series, smm_min: pd.Series, smm_max: pd.Series) -> pd.Series:
    """0 = extremo seco (frecuencia `smm_max`), 100 = extremo húmedo (frecuencia `smm_min`).

    Sin recortar: una lectura fuera del rango de la sonda da un valor fuera de 0 a 100.
    """
    span = (smm_max - smm_min).where(smm_max > smm_min)
    return (smm_max - frequency) / span * 100


def add_relative_moisture(daily: pd.DataFrame, units: pd.DataFrame) -> pd.DataFrame:
    """Añade el índice de humedad relativa a la tabla diaria usando el rango de cada unidad.

    La frecuencia máxima del día es el momento más seco, así que da el índice mínimo y al revés.
    Una unidad sin rango válido queda con valores nulos.
    """
    ranges = units.set_index("unit_id")[["smm_min", "smm_max"]].reindex(daily["unit_id"])
    smm_min = pd.Series(ranges["smm_min"].to_numpy(dtype=float), index=daily.index)
    smm_max = pd.Series(ranges["smm_max"].to_numpy(dtype=float), index=daily.index)
    return daily.assign(
        moisture_index_mean=moisture_index(daily["frequency_mean"], smm_min, smm_max),
        moisture_index_min=moisture_index(daily["frequency_max"], smm_min, smm_max),
        moisture_index_max=moisture_index(daily["frequency_min"], smm_min, smm_max),
    ).replace([np.inf, -np.inf], np.nan)
