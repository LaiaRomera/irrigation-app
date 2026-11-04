"""Pruebas del agrupado de unidades por ubicación, con datos sintéticos."""

import pandas as pd

from irrigation_app.weather.locations import LOCATION_COLUMNS, UNIT_LOCATION_COLUMNS, locations_from_units

UNITS = pd.DataFrame(
    {
        "unit_id": [1, 2, 3, 4],
        "latitude": [41.5569, 41.5571, 41.9884, 41.5569],
        "longitude": [0.3073, 0.3075, 0.2085, 0.9000],
        "coord_status": ["ok", "ok", "shared", "ok"],
    }
)


def test_units_within_about_a_kilometre_share_a_location():
    locations, unit_locations = locations_from_units(UNITS)
    ids = unit_locations.set_index("unit_id")["location_id"]
    assert ids[1] == ids[2]
    assert len({ids[1], ids[3], ids[4]}) == 3
    assert len(locations) == 3


def test_each_location_counts_its_units_and_flags_shared_coordinates():
    locations, _ = locations_from_units(UNITS)
    by_id = locations.set_index("location_id")
    assert by_id.loc["41.56_0.31", "n_units"] == 2
    assert not by_id.loc["41.56_0.31", "has_shared_coordinates"]
    assert by_id.loc["41.99_0.21", "has_shared_coordinates"]


def test_the_coordinates_to_request_are_the_rounded_ones():
    locations, _ = locations_from_units(UNITS)
    row = locations.set_index("location_id").loc["41.56_0.31"]
    assert (row["latitude"], row["longitude"]) == (41.56, 0.31)


def test_every_unit_gets_a_location_and_columns_are_stable():
    locations, unit_locations = locations_from_units(UNITS)
    assert list(locations.columns) == LOCATION_COLUMNS
    assert list(unit_locations.columns) == UNIT_LOCATION_COLUMNS
    assert sorted(unit_locations["unit_id"]) == [1, 2, 3, 4]
