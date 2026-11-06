"""Pruebas de la redacción del texto (7.2): LLM simulado, validación de cifras y plantilla de respaldo."""

import json

import pytest
from fixtures.engine_samples import PLOT_LABEL, sample_results, typical_error

from irrigation_app.reports.content import build_report_content
from irrigation_app.reports.llm import (
    FakeLlmClient,
    LlmGenerator,
    TemplateGenerator,
    TextGenerationError,
    generate_text,
    load_prompt,
    parse_llm_json,
)
from irrigation_app.reports.validation import validate_fields

RESULTS = sample_results()


def make_content(name="cruza_dia_4", report_type="daily"):
    return build_report_content(RESULTS[name], report_type=report_type, plot_label=PLOT_LABEL,
                                typical_error=typical_error())


def ok_response(**overrides) -> str:
    fields = {
        "summary": "No se riega hoy; el umbral se alcanzaría hacia el día 4, con una dosis de 17,6 mm netos.",
        "reason": "El cultivo está en etapa media y la ET₀ prevista es de 5,0 mm.",
        "warning": "Es una recomendación indicativa y el día no es exacto: decide la lectura real de la sonda.",
    }
    fields.update(overrides)
    return json.dumps(fields, ensure_ascii=False)


# --- Plantilla de respaldo ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", list(RESULTS))
@pytest.mark.parametrize("report_type", ["daily", "weekly"])
def test_template_text_only_uses_engine_figures(name, report_type):
    content = make_content(name, report_type)
    text = TemplateGenerator().generate(content)
    assert text.generator == "template" and text.generator_version == "template-1"
    check = validate_fields({"summary": text.summary, "reason": text.reason, "warning": text.warning}, content.figures())
    assert check.ok, check.reason


def test_template_text_has_decision_figures_and_limitations():
    text = TemplateGenerator().generate(make_content("regar_hoy"))
    assert "regar" in text.summary and "17,9 mm" in text.summary and "franja mojada" in text.summary
    assert "indicativa" in text.warning.lower()
    assert "cubierta vegetal" in text.warning and "intervalo de 3 días" in text.warning


def test_template_never_presents_the_crossing_day_as_exact():
    text = TemplateGenerator().generate(make_content("cruza_dia_4"))
    assert "no es un día exacto" in text.summary and "rango entre el día 2 y el día 5" in text.summary
    assert "lectura real de la sonda" in text.summary


def test_template_for_missing_data_says_so_without_dose():
    text = TemplateGenerator().generate(make_content("datos_insuficientes"))
    assert "datos insuficientes" in text.summary and " mm" not in text.summary


# --- LLM simulado ------------------------------------------------------------------------------------
def test_valid_llm_answer_is_used():
    client = FakeLlmClient(ok_response())
    text = generate_text(make_content(), LlmGenerator(client, timeout_s=7))
    assert text.generator == "llm" and text.fallback_reason is None
    assert text.generator_version == "fake/fake-1/agronomist_v1"
    assert "17,6 mm" in text.summary
    assert client.calls[0]["timeout_s"] == 7


def test_llm_receives_the_prompt_and_only_engine_facts():
    client = FakeLlmClient(ok_response())
    generate_text(make_content(), LlmGenerator(client))
    call = client.calls[0]
    assert "calculas nada" in call["system"] and "<!--" not in call["system"]
    facts = json.loads(call["user"])
    assert facts["decision"] == "do_not_irrigate" and facts["outlook"]["day"] == 4
    assert "text" not in facts and "today" not in facts


def test_changed_figure_falls_back_with_reason():
    text = generate_text(make_content(), LlmGenerator(FakeLlmClient(ok_response(summary="Regar 18 mm netos el día 4."))))
    assert text.generator == "template"
    assert text.fallback_reason.startswith("validation_failed") and "18 mm" in text.fallback_reason


def test_invented_figure_falls_back():
    text = generate_text(make_content(), LlmGenerator(FakeLlmClient(ok_response(reason="Sin riego se pierde un 15 % de cosecha."))))
    assert text.generator == "template" and "15 %" in text.fallback_reason


@pytest.mark.parametrize("raw, reason", [
    ("esto no es json", "invalid_json"),
    ("[1, 2]", "invalid_json"),
    (json.dumps({"summary": "a", "reason": "b"}), "missing_fields:warning"),
    (json.dumps({"summary": "a", "reason": "", "warning": "c"}), "missing_fields:reason"),
    (ok_response(summary="<p>Regar</p>"), "html_in_summary"),
    (ok_response(warning="x" * 2000), "too_long:warning"),
])
def test_bad_structure_falls_back(raw, reason):
    text = generate_text(make_content(), LlmGenerator(FakeLlmClient(raw)))
    assert text.generator == "template" and text.fallback_reason == reason


def test_json_inside_a_code_fence_is_accepted():
    assert parse_llm_json("```json\n" + ok_response() + "\n```")["summary"].startswith("No se riega")


def test_api_error_falls_back_without_leaking_details():
    secret = "sk-SECRETO C:\\ruta\\privada"
    text = generate_text(make_content(), LlmGenerator(FakeLlmClient(TimeoutError(secret))))
    assert text.generator == "template" and text.fallback_reason == "api_error:TimeoutError"
    assert "sk-" not in repr(text) and "privada" not in repr(text)


def test_no_client_configured_uses_template():
    text = generate_text(make_content(), None)
    assert text.generator == "template" and text.fallback_reason == "no_llm_configured"


def test_unexpected_generator_error_falls_back():
    class Broken(LlmGenerator):
        def generate(self, content):
            raise ValueError("clave=abc")

    text = generate_text(make_content(), Broken(FakeLlmClient("")))
    assert text.fallback_reason == "unexpected_error:ValueError" and "abc" not in repr(text)


def test_text_generation_error_carries_a_safe_code():
    with pytest.raises(TextGenerationError) as info:
        parse_llm_json("{}")
    assert info.value.reason == "missing_fields:summary,reason,warning"


def test_prompt_is_versioned_and_forbids_new_figures_html_and_web_search():
    prompt = load_prompt()
    for must in ("Toda cifra", "indicativa", "rango", "Sin HTML", "internet"):
        assert must in prompt
