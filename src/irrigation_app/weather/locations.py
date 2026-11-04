"""Agrupa las unidades por ubicación para pedir la meteorología una sola vez por sitio.

Open-Meteo trabaja con celdas de varios kilómetros, así que dos sondas a pocos metros reciben el
mismo tiempo. Se redondean las coordenadas y las unidades que coinciden comparten ubicación.
Las coordenadas que se envían a Open-Meteo son las redondeadas, no las exactas de la finca.
"""

from __future__ import annotations

import pandas as pd

# Dos decimales son unos 1,1 km de latitud: menos que la resolución del modelo meteorológico.
COORDINATE_DECIMALS = 2

LOCATION_COLUMNS = ["location_id", "latitude", "longitude", "n_units", "has_shared_coordinates"]
UNIT_LOCATION_COLUMNS = ["unit_id", "location_id"]


def locations_from_units(
    units: pd.DataFrame, decimals: int = COORDINATE_DECIMALS
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve las ubicaciones distintas y qué ubicación tiene cada unidad.

    Una ubicación marca `has_shared_coordinates` si alguna de sus unidades lo tiene (varios clientes
    en el mismo sitio: puede ser una coordenada por defecto de la empresa y no la de la finca).
    """
    latitude = units["latitude"].round(decimals)
    longitude = units["longitude"].round(decimals)
    location_id = latitude.map(lambda v: f"{v:.{decimals}f}") + "_" + longitude.map(lambda v: f"{v:.{decimals}f}")

    unit_locations = pd.DataFrame({"unit_id": units["unit_id"], "location_id": location_id})
    locations = (
        pd.DataFrame(
            {
                "location_id": location_id,
                "latitude": latitude,
                "longitude": longitude,
                "shared": units["coord_status"] == "shared",
            }
        )
        .groupby("location_id", sort=True)
        .agg(latitude=("latitude", "first"), longitude=("longitude", "first"), n_units=("shared", "size"),
             has_shared_coordinates=("shared", "any"))
        .reset_index()
    )
    return locations[LOCATION_COLUMNS], unit_locations[UNIT_LOCATION_COLUMNS]
