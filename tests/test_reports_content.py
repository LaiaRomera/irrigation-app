"""Pruebas del contenido del informe y del error típico por horizonte."""

import datetime as dt

import pytest
from fixtures.engine_samples import PLOT_LABEL, SYNTHETIC_METRICS_CSV, sample_results, typical_error

from irrigation_app.reports.content import build_report_content, format_date, format_number
from irrigation_app.reports.metrics import load_typical_error
from irrigation_app.reports.validation import extract_figures


@pytest.fixture(scope="module")
def results():
    return sample_results()


def content(result, report_type="daily", **kw):
    return build_report_content(result, report_type=report_type, plot_label=PLOT_LABEL, **kw)


def test_formats_use_spanish_conventions():
    assert format_number(17.6) == "17,6"
    assert format_number(None) == "—"
    assert format_date(dt.date(2026, 8, 25)) == "25 de agosto de 2026"


def test_daily_has_only_the_first_day_and_weekly_adds_six(results):
    daily = content(results["cruza_dia_4"], "daily")
    weekly = content(results["cruza_dia_4"], "weekly")
    assert daily.other_days == ()
    assert [d.day for d in weekly.other_days] == [2, 3, 4, 5, 6, 7]
    assert weekly.first_day == daily.first_day
    day4 = weekly.other_days[2]
    assert day4.decision == "irrigate" and day4.net_dose_mm == results["cruza_dia_4"].net_dose_mm


def test_figures_are_copied_from_the_engine(results):
    r = results["regar_hoy"]
    c = content(r)
    assert c.decision == "irrigate"
    assert c.first_day.net_dose_mm == r.plan[0].net_dose_mm
    assert c.first_day.gross_dose_mm == r.plan[0].gross_dose_mm
    assert c.indicative is True and c.validation_status == r.validation_status


def test_outlook_gives_a_range_not_an_exact_day(results):
    c = content(results["cruza_dia_4"])
    assert c.outlook is not None
    assert c.outlook.day == 4
    assert c.outlook.day_min <= 4 <= c.outlook.day_max
    assert "no es exacto" in c.uncertainty_note
    assert content(results["suelo_humedo"]).outlook is None


def test_limitations_flag_the_ones_the_engine_asked_to_show(results):
    c = content(results["cruza_dia_4"])
    highlighted = {lim.code for lim in c.limitations if lim.highlighted}
    assert {"assumed_ground_cover", "assumed_crop_stage", "assumed_replenishment_interval",
            "upper_anchor_above_observed_max", "localized_irrigation"} <= highlighted
    assert c.dose_on_wetted_strip is True
    assert "out_of_season" in {lim.code for lim in content(results["fuera_de_campana"]).limitations}


def test_old_report_is_marked_stale(results):
    r = results["cruza_dia_4"]
    assert not content(r, today=r.origin_date).is_stale
    assert content(r, today=r.origin_date + dt.timedelta(days=2)).is_stale
    assert not content(r).is_stale  # sin fecha de consulta no se puede decir


def test_insufficient_data_keeps_the_mark(results):
    c = content(results["datos_insuficientes"])
    assert c.decision == "insufficient_data" and c.validation_status == "insufficient_data"
    assert not c.has_dose


def test_load_typical_error_reads_lightgbm_test_all():
    errors = load_typical_error(SYNTHETIC_METRICS_CSV)
    assert [e.horizon_days for e in errors] == [1, 2, 3, 4, 5, 6, 7]
    assert errors[0].mae == 0.66 and errors[6].mae == 1.51
    assert all(e.method == "lightgbm" and e.split == "test" for e in errors)  # no mezcla validación ni no_shared


def test_missing_metrics_file_gives_no_figures(tmp_path):
    assert load_typical_error(tmp_path / "no_existe.csv") == ()


def test_typical_error_is_allowed_in_text(results):
    c = content(results["cruza_dia_4"], typical_error=typical_error())
    assert c.figures().allows(extract_figures("0,66 puntos")[0])
    assert not c.figures().allows(extract_figures("0,67 puntos")[0])
