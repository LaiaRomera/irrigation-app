"""Servicio de informes (7.4): permiso, motor, recomendación versionada, texto y PDF contra el PostgreSQL local."""

import datetime as dt
import json

import pytest
from fixtures.service_seed import add_plot, add_user, seed
from irrigation_engine_factory import ORIGIN

from irrigation_app.reports import service
from irrigation_app.reports.llm import FakeLlmClient, LlmGenerator
from irrigation_app.reports.metrics import typical_error_from_model_metrics
from irrigation_app.reports.pdf import find_browser
from irrigation_app.reports.service import AccessDenied, ReportError, generate_report

GOOD_LLM = json.dumps({
    "summary": "No se riega hoy; el umbral se alcanzaría hacia el día 4, con una dosis de 17,6 mm netos.",
    "reason": "El cultivo está en etapa media y la ET₀ prevista es de 5,0 mm.",
    "warning": "Es una recomendación indicativa y el día no es exacto: decide la lectura real de la sonda.",
}, ensure_ascii=False)


def llm(response=GOOD_LLM):
    return LlmGenerator(FakeLlmClient(response), system_prompt="prompt de prueba")


def count(connection, table):
    return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


@pytest.fixture
def seeded(pg_connection):
    return seed(pg_connection)


def generate(pg_connection, seeded, report_type="daily", **kw):
    kw.setdefault("today", ORIGIN)
    return generate_report(seeded.plot_id, seeded.owner_id, report_type, connection=pg_connection, **kw)


# --- Sin base de datos --------------------------------------------------------------------------
def test_unknown_report_type_is_rejected_before_touching_the_database():
    with pytest.raises(ReportError) as error:
        generate_report(1, 1, "mensual")
    assert error.value.code == "invalid_report_type"


def test_errors_carry_fixed_safe_messages():
    for code in service.MESSAGES:
        message = ReportError(code).message
        assert "\\" not in message and "password" not in message.lower() and "Traceback" not in message


def test_typical_error_from_model_metrics_reads_the_c4_shape_and_skips_missing():
    rows = [(2, {"typical_error": {"split": "test", "mae": 0.8, "rmse": 1.6, "n": 10}}), (1, None), (3, {"typical_error": {}}),
            (4, {"typical_error": {"mae": 1.0}})]
    errors = typical_error_from_model_metrics(rows)
    assert [(e.horizon_days, e.mae, e.rmse, e.n) for e in errors] == [(2, 0.8, 1.6, 10), (4, 1.0, None, None)]


# --- Informe correcto ---------------------------------------------------------------------------
@pytest.mark.integration
def test_daily_report_is_stored_with_the_engine_figures(pg_connection, seeded):
    outcome = generate(pg_connection, seeded)
    report = outcome.report
    assert outcome.status == "generated" and outcome.origin_date == ORIGIN
    assert report.created and not report.recommendation_reused and report.recommendation_version == 1
    assert report.content.report_type == "daily" and report.content.other_days == ()
    assert report.content.first_day.day == 1 and report.content.first_day.date == ORIGIN + dt.timedelta(days=1)
    assert report.content.indicative and report.content.typical_error and report.content.typical_error[0].mae == 0.66
    assert report.generator == "template" and report.validation_checks["fallback_reason"] == "no_llm_configured"
    assert report.validation_checks["figures_ok"] is True
    row = pg_connection.execute("SELECT decision, validation_status, method_version, jsonb_array_length(daily_plan) FROM recommendation").fetchone()
    assert row[0] == report.content.decision and row[2].startswith("fao56-engine") and row[3] == 7
    text_es, content = pg_connection.execute("SELECT text_es, content FROM report").fetchone()
    assert report.content.text.summary in text_es and "today" not in content


@pytest.mark.integration
def test_weekly_report_adds_the_other_six_days(pg_connection, seeded):
    report = generate(pg_connection, seeded, "weekly").report
    assert report.content.report_type == "weekly" and len(report.content.other_days) == 6
    assert [d.day for d in report.content.other_days] == [2, 3, 4, 5, 6, 7]


# --- Permisos -----------------------------------------------------------------------------------
@pytest.mark.integration
def test_a_plot_of_another_account_is_rejected_and_nothing_is_written(pg_connection, seeded):
    with pytest.raises(AccessDenied) as foreign:
        generate_report(seeded.foreign_plot_id, seeded.owner_id, "daily", connection=pg_connection)
    with pytest.raises(AccessDenied) as missing:
        generate_report(999999, seeded.owner_id, "daily", connection=pg_connection)
    assert foreign.value.message == missing.value.message  # no se revela si existe
    assert count(pg_connection, "recommendation") == 0 and count(pg_connection, "report") == 0


@pytest.mark.integration
def test_inactive_user_or_inactive_plot_have_no_access(pg_connection, seeded):
    pg_connection.execute("UPDATE plot SET is_active = FALSE WHERE plot_id = %s", (seeded.plot_id,))
    with pytest.raises(AccessDenied):
        generate(pg_connection, seeded)
    pg_connection.execute("UPDATE plot SET is_active = TRUE WHERE plot_id = %s", (seeded.plot_id,))
    pg_connection.execute("UPDATE app_user SET is_active = FALSE WHERE user_id = %s", (seeded.owner_id,))
    with pytest.raises(AccessDenied):
        generate(pg_connection, seeded)


@pytest.mark.integration
def test_plot_code_selects_only_among_the_users_own_plots(pg_connection, seeded):
    assert service.find_user_plot(pg_connection, seeded.owner_id, "abcdefgh").plot_id == seeded.plot_id
    assert service.find_user_plot(pg_connection, seeded.owner_id, "JKMNPQRS") is None  # código de otra cuenta
    assert {p.plot_id for p in service.list_user_plots(pg_connection, seeded.owner_id)} == {seeded.plot_id, seeded.generic_plot_id}


@pytest.mark.integration
def test_a_report_can_only_be_read_by_its_owner(pg_connection, seeded):
    report = generate(pg_connection, seeded).report
    assert service.get_report(pg_connection, report.report_id, seeded.owner_id, today=ORIGIN).report_id == report.report_id
    with pytest.raises(AccessDenied):
        service.get_report(pg_connection, report.report_id, seeded.other_id)
    with pytest.raises(AccessDenied):
        service.build_report_pdf(pg_connection, report.report_id, seeded.other_id)


# --- Dos informes de la misma recomendación -------------------------------------------------------
@pytest.mark.integration
def test_llm_and_template_reports_share_the_same_recommendation(pg_connection, seeded):
    with_llm = generate(pg_connection, seeded, text_generator=llm()).report
    template = generate(pg_connection, seeded).report
    assert with_llm.generator == "llm" and with_llm.content.text.fallback_reason is None
    assert template.generator == "template"
    assert with_llm.recommendation_id == template.recommendation_id and template.recommendation_reused
    assert count(pg_connection, "recommendation") == 1 and count(pg_connection, "report") == 2
    assert with_llm.validation_checks["figures_ok"] is True


@pytest.mark.integration
def test_llm_text_with_a_wrong_figure_falls_back_to_the_template_with_the_reason(pg_connection, seeded):
    wrong = json.dumps({"summary": "Regar 18 mm hoy.", "reason": "Motivo.", "warning": "Indicativa."}, ensure_ascii=False)
    report = generate(pg_connection, seeded, text_generator=llm(wrong)).report
    assert report.generator == "template" and report.validation_checks["fallback_reason"].startswith("validation_failed")
    assert "18 mm" not in report.content.text.summary


@pytest.mark.integration
def test_repeating_returns_the_stored_report_without_duplicating(pg_connection, seeded):
    first = generate(pg_connection, seeded).report
    again = generate(pg_connection, seeded).report
    assert first.created and not again.created and again.report_id == first.report_id
    assert (count(pg_connection, "recommendation"), count(pg_connection, "report")) == (1, 1)


@pytest.mark.integration
def test_changed_inputs_create_a_new_recommendation_version_linked_to_the_previous(pg_connection, seeded):
    first = generate(pg_connection, seeded).report
    pg_connection.execute("UPDATE forecast_data SET rainmm = 12 WHERE plot_id = %s AND fecha = %s", (seeded.plot_id, ORIGIN + dt.timedelta(days=1)))
    second = generate(pg_connection, seeded).report
    assert second.recommendation_version == 2 and second.recommendation_id != first.recommendation_id
    assert pg_connection.execute("SELECT supersedes_id FROM recommendation WHERE version = 2").fetchone()[0] == first.recommendation_id
    assert count(pg_connection, "recommendation") == 2


# --- Intervalo de reposición, campaña y antigüedad ----------------------------------------------------
@pytest.mark.integration
@pytest.mark.parametrize(("interval", "expected"), [(None, 3), (2, 2), (5, 5)])
def test_plot_interval_is_passed_to_the_engine(pg_connection, interval, expected):
    seeded = seed(pg_connection, interval=interval, levels={k: 55.0 - k for k in range(1, 8)})  # ya bajo el umbral: riega
    report = generate(pg_connection, seeded).report
    assert report.content.decision == "irrigate"
    assert report.content.replenishment_interval_days == expected
    snapshot = pg_connection.execute("SELECT inputs_snapshot FROM recommendation").fetchone()[0]
    assert snapshot["engine_input"]["replenishment_interval_days"] == interval


@pytest.mark.integration
def test_out_of_season_origin_does_not_recommend_irrigation(pg_connection):
    origin = dt.date(2026, 10, 20)
    seeded = seed(pg_connection, origin=origin)
    report = generate_report(seeded.plot_id, seeded.owner_id, "daily", connection=pg_connection, today=origin).report
    assert report.content.decision == "do_not_irrigate"
    assert any(lim.code == "out_of_season" for lim in report.content.limitations)


@pytest.mark.integration
def test_an_old_report_shows_its_date_and_is_never_presented_as_current(pg_connection, seeded):
    report = generate(pg_connection, seeded, today=ORIGIN + dt.timedelta(days=3)).report
    assert report.is_stale and report.content.is_stale and report.origin_date == ORIGIN
    reread = service.get_report(pg_connection, report.report_id, seeded.owner_id, today=ORIGIN)
    assert not reread.is_stale  # mismo informe leído el día de su origen
    assert "today" not in pg_connection.execute("SELECT content FROM report").fetchone()[0]


# --- Parcelas sin datos de cultivo, sin predicción ----------------------------------------------------
@pytest.mark.integration
def test_a_plot_without_crop_data_gets_no_irrigation_recommendation(pg_connection, seeded):
    outcome = generate_report(seeded.generic_plot_id, seeded.owner_id, "daily", connection=pg_connection, today=ORIGIN)
    assert outcome.status == "crop_data_unavailable" and outcome.report is None
    assert "no disponibles" in outcome.message and "no se emite recomendación de riego" in outcome.message
    assert len(outcome.moisture_forecast) == 28 and {r["depth_cm"] for r in outcome.moisture_forecast} == {15, 25, 35, 55}
    assert count(pg_connection, "recommendation") == 0 and count(pg_connection, "report") == 0


@pytest.mark.integration
def test_a_plot_without_predictions_returns_no_prediction_and_does_not_compute(pg_connection):
    owner = add_user(pg_connection, "solo@example.com")
    plot_id, _ = add_plot(pg_connection, owner, "Vacía", "ZZZZZZZZ", unit=1168)
    pg_connection.commit()
    outcome = generate_report(plot_id, owner, "daily", connection=pg_connection)
    assert outcome.status == "no_prediction" and outcome.report is None
    assert count(pg_connection, "recommendation") == 0


@pytest.mark.integration
def test_missing_forecast_is_a_null_not_an_invented_number(pg_connection):
    seeded = seed(pg_connection)
    pg_connection.execute("DELETE FROM forecast_data WHERE plot_id = %s AND fecha = %s", (seeded.plot_id, ORIGIN + dt.timedelta(days=7)))
    report = generate_report(seeded.plot_id, seeded.owner_id, "weekly", connection=pg_connection, today=ORIGIN).report
    assert report.content.other_days[-1].rain_mm is None


# --- PDF ---------------------------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.skipif(find_browser() is None, reason="no hay Edge/Chrome para imprimir")
def test_pdf_is_generated_from_the_stored_report(pg_connection, seeded):
    report = generate(pg_connection, seeded, "weekly").report
    pdf = service.build_report_pdf(pg_connection, report.report_id, seeded.owner_id, today=ORIGIN)
    assert pdf.startswith(b"%PDF") and len(pdf) > 5000


@pytest.mark.integration
def test_template_text_passes_validation_with_unrounded_engine_values(pg_connection, seeded):
    """Regresión: con la ET0 real (5,56) el texto decía «5,6 mm» y la validación comparaba contra 5,56."""
    pg_connection.execute("UPDATE forecast_data SET eto = 5.56, rainmm = 3.14 WHERE plot_id = %s", (seeded.plot_id,))
    report = generate(pg_connection, seeded, "weekly").report
    assert report.validation_checks["figures_ok"] is True, report.validation_checks["problems"]
    assert report.content.first_day.eto_mm == 5.6 and report.content.first_day.rain_mm == 3.1
