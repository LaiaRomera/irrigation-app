"""La parcela de referencia se importa, se valida y rechaza confirmarse sin cultivo o textura."""

import datetime as dt

import pytest
from pydantic import ValidationError

from irrigation_app.reference_plot import REFERENCE_PLOT, Anchor, Anchors, InstallationDays, ReferencePlot, Sourced

INSTALLATION = InstallationDays(rule="regla", summary="resumen")


def _plot(**overrides):
    base = dict(
        unit_id=1,
        name="x",
        status="confirmed",
        crop=Sourced(value="manzano", source="laia"),
        soil_texture=Sourced(value="franco arcilloso limoso", source="laia"),
        average_depths_cm=(15, 25),
        sensor_depths_cm=(15, 25, 55),
        smm_min=6900,
        smm_max=11700,
        installation=INSTALLATION,
    )
    return ReferencePlot(**{**base, **overrides})


def test_reference_plot_imports_and_is_the_confirmed_1168():
    assert REFERENCE_PLOT.unit_id == 1168
    assert REFERENCE_PLOT.status == "confirmed"
    assert REFERENCE_PLOT.crop.value == "manzano" and REFERENCE_PLOT.crop.source == "laia"
    assert REFERENCE_PLOT.soil_texture.value == "franco arcilloso limoso"
    assert REFERENCE_PLOT.irrigation_type.value == "drip_one_line"
    assert REFERENCE_PLOT.average_depths_cm == (15, 25, 35)
    assert 55 not in REFERENCE_PLOT.average_depths_cm


def test_unknown_optional_values_are_none_not_guesses():
    assert REFERENCE_PLOT.cycle_start_date is None
    assert REFERENCE_PLOT.area_m2 is None


def test_anchors_come_from_agrodevices_and_are_confirmed_by_laia():
    anchors = REFERENCE_PLOT.anchors
    assert anchors.upper.source == anchors.lower.source == "agrodevices"
    assert anchors.upper.index == pytest.approx(79.396, abs=1e-3)
    assert anchors.lower.index == pytest.approx(59.708, abs=1e-3)
    for a in (anchors.upper, anchors.lower):
        assert a.review == "confirmed" and a.reviewed_by == "Laia" and a.reviewed_on == dt.date(2026, 11, 5)
    assert (anchors.upper.frequency, anchors.lower.frequency) == (7889, 8834)


def test_confirmed_anchor_needs_who_and_when():
    with pytest.raises(ValidationError, match="reviewed_by"):
        Anchor(index=50, source="data", method="m", review="confirmed")


def test_ground_cover_is_a_declared_assumption_of_laia():
    cover = REFERENCE_PLOT.ground_cover
    assert cover.value == "active_cover" and cover.source == "assumption"
    assert "sin confirmar" in cover.note and "1,20 a 0,95" in cover.note


def test_ground_cover_is_optional_and_validated():
    assert _plot().ground_cover is None
    assert _plot(ground_cover=Sourced(value="no_cover", source="laia")).ground_cover.value == "no_cover"
    with pytest.raises(ValidationError):
        _plot(ground_cover=Sourced(value="pasto", source="laia"))


@pytest.mark.parametrize("missing", ["crop", "soil_texture"])
def test_confirmed_requires_crop_and_texture(missing):
    with pytest.raises(ValidationError, match=missing):
        _plot(**{missing: None})


def test_candidate_can_lack_crop_and_texture():
    plot = _plot(status="candidate", crop=None, soil_texture=None)
    assert plot.status == "candidate"


def test_irrigation_type_cycle_and_area_are_optional():
    plot = _plot()
    assert plot.irrigation_type is None and plot.cycle_start_date is None and plot.area_m2 is None


def test_average_depths_must_be_sensor_depths():
    with pytest.raises(ValidationError, match="average_depths_cm"):
        _plot(average_depths_cm=(15, 45))


def test_upper_anchor_must_be_above_lower():
    mk = lambda i: Anchor(index=i, source="data", method="m")
    with pytest.raises(ValidationError, match="superior"):
        Anchors(upper=mk(50), lower=mk(60))


def test_source_must_be_known():
    with pytest.raises(ValidationError):
        Sourced(value="x", source="inventado")
