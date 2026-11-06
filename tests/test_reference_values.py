"""Las tablas FAO-56: valores conocidos, fuente citada y error claro ante lo desconocido."""

import pytest

from irrigation_app.irrigation_engine import reference_values as rv


def test_silty_clay_loam_taw_range_and_midpoint():
    assert rv.available_water_range_per_m("franco arcilloso limoso") == (130.0, 180.0)
    taw = rv.total_available_water_per_m("franco arcilloso limoso")
    assert taw.value == 155.0 and taw.unit == "mm/m"
    assert "Tabla 19" in taw.source and taw.status == "assumption"


def test_english_alias_and_case_are_accepted():
    assert rv.soil_texture("  Silty Clay Loam ") is rv.soil_texture("franco arcilloso limoso")


def test_field_capacity_and_wilting_point_midpoints():
    fc, wp = rv.field_capacity_and_wilting_point("franco arcilloso limoso")
    assert (fc.value, wp.value) == (0.335, 0.205)


def test_apple_values_from_tables_22_and_12():
    assert rv.crop("manzano").root_depth_m == (1.0, 2.0)
    assert rv.root_depth_for_scheduling("manzano").value == 1.0
    assert rv.depletion_fraction("manzano").value == 0.5
    assert rv.kc_values("manzano") == (0.45, 0.95, 0.70)
    assert rv.kc_values("manzano", "active_cover_no_frost") == (0.80, 1.20, 0.85)
    assert "Tabla 22" in rv.depletion_fraction("manzano").source
    assert "Tabla 12" in rv.crop("manzano").sources["kc"]


def test_kc_case_follows_ground_cover_and_assumes_killing_frost():
    assert rv.kc_case("active_cover") == "active_cover_killing_frost"
    assert rv.kc_case("no_cover") == "no_cover_killing_frost"
    assert rv.kc_case(None) == rv.DEFAULT_KC_CASE
    assert rv.kc_values("manzano", rv.kc_case("active_cover")) == (0.50, 1.20, 0.95)
    assert rv.kc_values("manzano", rv.kc_case("no_cover")) == (0.45, 0.95, 0.70)
    with pytest.raises(rv.UnknownReferenceError, match="Cubierta desconocida"):
        rv.kc_case("pasto")


def test_reference_plot_cover_selects_the_kc_of_table_12():
    from irrigation_app.reference_plot import REFERENCE_PLOT

    case = rv.kc_case(REFERENCE_PLOT.ground_cover.value)
    assert rv.kc_values(REFERENCE_PLOT.crop.value, case) == (0.50, 1.20, 0.95)


def test_dose_scales_with_kc_so_no_cover_is_lower():
    etc_active = rv.kc_values("manzano", rv.kc_case("active_cover"))[1] * 5.0
    etc_none = rv.kc_values("manzano", rv.kc_case("no_cover"))[1] * 5.0
    assert (etc_active, etc_none) == (pytest.approx(6.0), pytest.approx(4.75))


def test_ground_cover_limitation_text_states_both_kc():
    text = rv.ground_cover_limitation("manzano", "active_cover")
    assert "sin confirmar" in text and "1,20" in text and "0,95" in text and "-21%" in text


def test_depletion_fraction_adjusts_with_etc_and_is_bounded():
    assert rv.depletion_fraction("manzano", etc_mm_day=3).value == pytest.approx(0.58)
    assert rv.depletion_fraction("manzano", etc_mm_day=-100).value == 0.8
    assert rv.depletion_fraction("manzano", etc_mm_day=100).value == 0.1


def test_stage_at_follows_table_11_lengths():
    assert rv.stage_at("manzano", 0) == "ini"
    assert rv.stage_at("manzano", 20) == "dev"
    assert rv.stage_at("manzano", 90) == "mid"
    assert rv.stage_at("manzano", 209) == "late"
    assert rv.stage_at("manzano", 210) is None and rv.stage_at("manzano", -1) is None


def test_drip_efficiency_and_generic_assumption():
    assert rv.irrigation_efficiency("drip_one_line").value == 0.90
    assert rv.irrigation_efficiency("goteo").status == "verified"
    generic = rv.irrigation_efficiency(None)
    assert generic.value == 0.75 and generic.status == "assumption"


def test_drip_wetted_fraction_formula():
    assert rv.drip_exposed_wetted_fraction(fc=0.0).value == 0.35
    assert rv.drip_exposed_wetted_fraction(fc=0.5, fw=0.3).value == pytest.approx(0.2)
    with pytest.raises(ValueError, match="fc"):
        rv.drip_exposed_wetted_fraction(fc=1.5)


@pytest.mark.parametrize(
    "call, text",
    [
        (lambda: rv.crop("kiwi"), "Cultivo desconocido"),
        (lambda: rv.soil_texture("arcilla pesada"), "Textura desconocida"),
        (lambda: rv.irrigation_efficiency("pivot"), "Tipo de riego desconocido"),
        (lambda: rv.kc_values("manzano", "otro"), "Caso de Kc desconocido"),
        (lambda: rv.total_available_water_per_m("granito"), "Textura desconocida"),
    ],
)
def test_unknown_reference_gives_clear_error(call, text):
    with pytest.raises(rv.UnknownReferenceError, match=text):
        call()


def test_every_reference_has_a_source():
    for ref in (*rv.IRRIGATION_EFFICIENCY.values(), rv.GENERIC_IRRIGATION_EFFICIENCY, rv.WETTED_FRACTION_TRICKLE):
        assert ref.source
    for c in rv.CROPS.values():
        assert set(c.sources) == {"root_depth_m", "depletion_fraction", "kc", "stage_days"}


def test_fmt_es_uses_decimal_comma():
    assert rv.fmt_es(0.5, 2) == "0,50" and rv.fmt_es(76.7, 1) == "76,7" and rv.fmt_es(0.35) == "0,35"
    assert rv.fmt_es(3) == "3"


def test_reference_notes_use_decimal_comma():
    import re
    notes = [
        rv.ground_cover_limitation("manzano", "active_cover"), rv.ground_cover_limitation("manzano", "no_cover"),
        rv.depletion_fraction("manzano", etc_mm_day=3.5).note, rv.root_depth_for_scheduling("manzano").note,
        rv.drip_exposed_wetted_fraction(0.5).note,
        *(r.note for r in rv.field_capacity_and_wilting_point("franco arcilloso limoso")),
    ]
    assert all(not re.search(r"\d\.\d", n) for n in notes)
