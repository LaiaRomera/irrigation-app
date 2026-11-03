"""Agregación de lecturas al formato diario que usa el modelo.

Trabaja sobre la tabla larga común (`unit_id`, `timestamp` UTC, `depth_cm`, `frequency`,
`reading_type`), sea cual sea la fuente que la produjo.
"""

from __future__ import annotations

import pandas as pd

# Tipos de lectura que se cuentan por separado (Agrodevices): real, interpolada y de administración.
REAL_TYPE = "SMM"
INTERPOLATED_TYPE = "SMMi"
ADMIN_TYPE = "SMMAdmin"

DAILY_COLUMNS = [
    "unit_id", "day", "depth_cm", "frequency_mean", "frequency_min", "frequency_max",
    "n_readings", "n_real", "n_interpolated", "n_admin",
]


def aggregate_daily(readings: pd.DataFrame) -> pd.DataFrame:
    """Una fila por unidad, día (UTC) y profundidad, con estadísticos y recuento de lecturas por tipo.

    Los recuentos permiten decidir después con cuántas lecturas reales se fía uno de un día;
    aquí no se descarta ningún día.
    """
    grouped = readings.assign(
        day=readings["timestamp"].dt.floor("D"),
        n_real=(readings["reading_type"] == REAL_TYPE).astype("uint8"),
        n_interpolated=(readings["reading_type"] == INTERPOLATED_TYPE).astype("uint8"),
        n_admin=(readings["reading_type"] == ADMIN_TYPE).astype("uint8"),
    ).groupby(["unit_id", "day", "depth_cm"], sort=True)

    daily = grouped.agg(
        frequency_mean=("frequency", "mean"),
        frequency_min=("frequency", "min"),
        frequency_max=("frequency", "max"),
        n_readings=("frequency", "size"),
        n_real=("n_real", "sum"),
        n_interpolated=("n_interpolated", "sum"),
        n_admin=("n_admin", "sum"),
    ).reset_index()
    return daily[DAILY_COLUMNS]
