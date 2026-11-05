"""Pruebas del reparto por ubicación y fecha de corte, con datos sintéticos."""

import pandas as pd
import pytest

from irrigation_app.models.split import (
    DUBIOUS_LOCATION,
    TEST,
    TRAIN,
    VALIDATION,
    assign_split,
    load_split,
    make_split,
    save_split,
)

CUTOFF = "2026-01-31"
N_LOCATIONS = 10
DAYS = pd.date_range("2025-12-01", "2026-03-15", freq="D", tz="UTC")


def make_world():
    """10 ubicaciones reales (1 unidad cada una) + la dudosa con 3 unidades; horizontes 1 y 7."""
    unit_locations = pd.DataFrame(
        {
            "unit_id": list(range(1, N_LOCATIONS + 1)) + [101, 102, 103],
            "location_id": [f"L{i}" for i in range(1, N_LOCATIONS + 1)] + [DUBIOUS_LOCATION] * 3,
        }
    )
    table = pd.DataFrame(
        [(u, d, h) for u in unit_locations["unit_id"] for d in DAYS for h in (1, 7)],
        columns=["unit_id", "origin_day", "horizon_days"],
    )
    return table, unit_locations


def split_and_labels(seed=42):
    table, unit_locations = make_world()
    split = make_split(table, unit_locations, CUTOFF, seed=seed)
    return table, unit_locations, split, assign_split(table, unit_locations, split)


def targets(table):
    return table["origin_day"] + pd.to_timedelta(table["horizon_days"], unit="D")


def test_no_training_target_after_the_cutoff():
    table, _, split, labels = split_and_labels()
    train = table[labels == TRAIN]
    assert len(train) > 0
    assert (targets(train) <= split.cutoff_day).all()


def test_no_validation_or_test_origin_before_cutoff_plus_7_days():
    table, _, split, labels = split_and_labels()
    limit = split.cutoff_day + pd.Timedelta(days=7)
    for name in (VALIDATION, TEST):
        part = table[labels == name]
        assert len(part) > 0
        assert (part["origin_day"] >= limit).all()


def test_holdout_locations_never_appear_in_train_or_validation():
    table, unit_locations, split, labels = split_and_labels()
    assert split.holdout_locations
    location = table["unit_id"].map(unit_locations.set_index("unit_id")["location_id"])
    held = location.isin(split.holdout_locations)
    assert not labels[held].isin([TRAIN, VALIDATION]).any()
    assert set(labels[held].dropna()) == {TEST}
    assert not labels[~held].eq(TEST).any()


def test_dubious_location_is_never_held_out_for_any_seed():
    table, unit_locations = make_world()
    for seed in range(30):
        split = make_split(table, unit_locations, CUTOFF, seed=seed)
        assert DUBIOUS_LOCATION not in split.holdout_locations
        assert DUBIOUS_LOCATION in split.train_locations
        labels = assign_split(table, unit_locations, split)
        dubious = table["unit_id"].isin([101, 102, 103])
        assert not labels[dubious].eq(TEST).any()
        assert set(labels[dubious].dropna()) == {TRAIN, VALIDATION}


def test_holdout_is_about_20_percent_of_real_locations():
    _, _, split, _ = split_and_labels()
    assert len(split.holdout_locations) == 2  # 20 % de 10 ubicaciones reales


def test_same_seed_same_split_and_different_seed_may_differ():
    table, unit_locations = make_world()
    a = make_split(table, unit_locations, CUTOFF, seed=1)
    b = make_split(table, unit_locations, CUTOFF, seed=1)
    assert a == b
    assert any(
        make_split(table, unit_locations, CUTOFF, seed=s).holdout_locations != a.holdout_locations for s in range(2, 12)
    )


def test_location_without_data_on_both_sides_is_not_a_candidate():
    table, unit_locations = make_world()
    # L1 solo tiene datos antes del corte.
    table = table[~((table["unit_id"] == 1) & (table["origin_day"] > pd.Timestamp("2026-01-10", tz="UTC")))]
    for seed in range(20):
        assert "L1" not in make_split(table, unit_locations, CUTOFF, seed=seed).holdout_locations


def test_margin_examples_are_left_unassigned():
    table, _, split, labels = split_and_labels()
    gap = (table["origin_day"] > split.cutoff_day - pd.Timedelta(days=1)) & (
        table["origin_day"] < split.cutoff_day + pd.Timedelta(days=7)
    )
    in_gap_with_late_target = gap & (targets(table) > split.cutoff_day)
    assert labels[in_gap_with_late_target].isna().all()


def test_unit_without_location_raises():
    table, unit_locations = make_world()
    split = make_split(table, unit_locations, CUTOFF)
    with pytest.raises(ValueError):
        assign_split(table.assign(unit_id=999), unit_locations, split)


def test_save_and_load_roundtrip(tmp_path):
    table, unit_locations = make_world()
    split = make_split(table, unit_locations, CUTOFF)
    path = tmp_path / "ml" / "split.json"
    save_split(split, path)
    assert load_split(path) == split


def test_load_split_reads_example_file_and_assigns(tmp_path):
    """Un split.json escrito a mano (formato del archivo real) se carga y reproduce la asignación."""
    import json

    table, unit_locations = make_world()
    example = {
        "cutoff": CUTOFF,
        "margin_days": 7,
        "seed": 42,
        "holdout_fraction": 0.2,
        "holdout_locations": ["L3", "L8"],
        "train_locations": [f"L{i}" for i in range(1, 11) if i not in (3, 8)] + [DUBIOUS_LOCATION],
    }
    path = tmp_path / "split.json"
    path.write_text(json.dumps(example), encoding="utf-8")

    split = load_split(path)
    assert split.holdout_locations == ["L3", "L8"]
    assert split.dubious_location == DUBIOUS_LOCATION
    labels = assign_split(table, unit_locations, split)
    held = unit_locations.set_index("unit_id")["location_id"].reindex(table["unit_id"]).isin(["L3", "L8"]).to_numpy()
    assert set(labels[held].dropna()) == {TEST}
    assert TEST not in set(labels[~held].dropna())
    assert labels[~held].isin([TRAIN, VALIDATION]).any()
