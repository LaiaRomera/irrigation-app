"""Pruebas del relleno de la plantilla, las gráficas y el PDF (7.3). Sin red, sin claves y sin descargas."""

import datetime as dt
from pathlib import Path

import pytest
from fixtures.engine_samples import PLOT_LABEL, SYNTHETIC_TEMPLATE_DIR, sample_inputs, typical_error

from irrigation_app.irrigation_engine import run_engine
from irrigation_app.reports import charts
from irrigation_app.reports.content import build_report_content
from irrigation_app.reports.llm import generate_text
from irrigation_app.reports.pdf import (
    BrowserCliPrinter,
    PdfGenerationError,
    PdfPrinter,
    PdfUnavailableError,
    PlaywrightPrinter,
    find_browser,
    print_pdf,
)
from irrigation_app.reports.render import TEMPLATE_DIR_ENV, make_charts, render_report_html, resolve_template_dir

INPUTS = sample_inputs()
PNG = b"\x89PNG\r\n\x1a\n"


def build(name, report_type="daily", today=None, with_charts=True):
    result = run_engine(INPUTS[name])
    content = build_report_content(result, report_type=report_type, plot_label=PLOT_LABEL,
                                   typical_error=typical_error(), today=today)
    content = content.with_text(generate_text(content))
    images = make_charts(result, INPUTS[name], content) if with_charts else {}
    return content, images


# --- Gráficas ----------------------------------------------------------------------------------------
def test_charts_are_png_images():
    result = run_engine(INPUTS["cruza_dia_4"])
    data = charts.chart_data(result, INPUTS["cruza_dia_4"])
    assert sorted(data.per_depth) == [15, 25, 35] and data.irrigation_day == 4
    assert data.upper_anchor == pytest.approx(79.4, abs=0.05) and data.lower_anchor == pytest.approx(59.7, abs=0.05)
    for png in (charts.moisture_chart(data), charts.plan_chart(data), charts.error_chart(typical_error())):
        assert png.startswith(PNG) and len(png) > 5000
    assert charts.to_data_uri(PNG).startswith("data:image/png;base64,")


def test_threshold_follows_the_engine_formula():
    result = run_engine(INPUTS["cruza_dia_4"])
    data = charts.chart_data(result)
    day = result.plan[0]
    assert data.threshold[0] == pytest.approx(79.4 - day.p * (79.4 - 59.7), abs=0.05)
    assert data.per_depth == {}  # sin la entrada del motor no hay series por profundidad


def test_charts_handle_days_without_data():
    result = run_engine(INPUTS["datos_insuficientes"])
    data = charts.chart_data(result)
    assert all(v is None for v in data.mean)
    assert charts.moisture_chart(data).startswith(PNG) and charts.plan_chart(data).startswith(PNG)


# --- HTML --------------------------------------------------------------------------------------------
def test_builtin_template_shows_decision_dose_error_and_indicative_mark():
    content, images = build("regar_hoy", "daily")
    rendered = render_report_html(content, images)
    html = rendered.html
    assert rendered.template_source == "builtin_minimal"
    assert "INDICATIVA" in html and "Regar" in html and "17,9 mm" in html
    assert "0,66" in html and "1,51" in html  # error típico por horizonte
    assert "franja mojada" in html and "assumed_ground_cover" in html and "assumed_replenishment_interval" in html
    assert "data:image/png;base64," in html
    assert "PLANTILLA MÍNIMA PROPIA" in html and "PUNTO DE SUSTITUCIÓN" in html


def test_insufficient_data_is_clearly_marked_and_has_no_dose():
    content, images = build("datos_insuficientes")
    html = render_report_html(content, images).html
    assert "insufficient_data" in html and "DATOS INSUFICIENTES" in html
    assert "sin dosis este día" in html and "faltan datos" in html


def test_daily_has_no_week_table_and_weekly_has_it():
    daily = render_report_html(*_pair("cruza_dia_4", "daily")).html
    weekly = render_report_html(*_pair("cruza_dia_4", "weekly")).html
    assert "Resto de la semana" not in daily and "Resto de la semana" in weekly
    assert "1 de septiembre de 2026" in weekly


def _pair(name, report_type):
    content, images = build(name, report_type)
    return content, images


def test_old_report_shows_its_date_and_a_warning():
    origin = INPUTS["cruza_dia_4"].origin_date
    old = render_report_html(*_pair_today("cruza_dia_4", origin + dt.timedelta(days=3))).html
    assert "no es el informe de hoy" in old and "25 de agosto de 2026" in old
    current = render_report_html(*_pair_today("cruza_dia_4", origin)).html
    assert "no es el informe de hoy" not in current


def _pair_today(name, today):
    content, images = build(name, today=today)
    return content, images


def test_missing_typical_error_is_said_not_invented():
    result = run_engine(INPUTS["cruza_dia_4"])
    content = build_report_content(result, plot_label=PLOT_LABEL).with_text(generate_text(
        build_report_content(result, plot_label=PLOT_LABEL)))
    html = render_report_html(content).html
    assert "no está disponible" in html


def test_html_is_escaped_so_text_cannot_inject_markup():
    content, images = build("cruza_dia_4")
    bad = content.text.model_copy(update={"summary": "<script>alert(1)</script>"})
    html = render_report_html(content.with_text(bad), images).html
    assert "<script>" not in html and "&lt;script&gt;" in html


# --- Plantilla externa (la de Agrodevices se lee de una ruta local) -------------------------------------
def test_external_template_is_filled_from_the_given_directory():
    content, images = build("regar_hoy")
    rendered = render_report_html(content, images, template_dir=SYNTHETIC_TEMPLATE_DIR)
    assert rendered.template_source == "agrodevices" and rendered.template_note == ""
    assert "PLANTILLA-SINTETICA" in rendered.html and "INDICATIVA" in rendered.html
    assert "[1:0,66]" in rendered.html and "[7:1,51]" in rendered.html
    assert "<base href=\"file:///" in rendered.html  # sus recursos se resuelven desde su carpeta


def test_template_dir_comes_from_the_environment_variable(monkeypatch):
    monkeypatch.setenv(TEMPLATE_DIR_ENV, str(SYNTHETIC_TEMPLATE_DIR))
    content, images = build("regar_hoy")
    assert render_report_html(content, images).template_source == "agrodevices"


def test_missing_external_template_falls_back_to_the_builtin_one_and_says_so(tmp_path):
    content, images = build("regar_hoy")
    rendered = render_report_html(content, images, template_dir=tmp_path / "no_existe")
    assert rendered.template_source == "builtin_minimal"
    assert "no está accesible" in rendered.template_note
    assert "no_existe" not in rendered.template_note  # no enseña rutas
    assert resolve_template_dir(None, environ={}) is None


def test_external_template_without_jinja_marks_is_reported(tmp_path):
    (tmp_path / "index.html").write_text("<html><head></head><body>fija</body></html>", encoding="utf-8")
    content, images = build("regar_hoy")
    rendered = render_report_html(content, images, template_dir=tmp_path)
    assert "no se rellenó ningún campo" in rendered.template_note


# --- PDF -----------------------------------------------------------------------------------------------
class FakePrinter(PdfPrinter):
    def __init__(self, payload: bytes = b"%PDF-1.4 falso"):
        self.payload, self.html = payload, None

    def print_pdf(self, html: str, out_path: Path) -> None:
        self.html = html
        out_path.write_bytes(self.payload)


def test_print_pdf_writes_a_non_empty_pdf_from_the_filled_html(tmp_path):
    content, images = build("regar_hoy")
    html = render_report_html(content, images, template_dir=SYNTHETIC_TEMPLATE_DIR).html
    printer = FakePrinter()
    out = print_pdf(html, tmp_path / "sub" / "informe.pdf", printer)
    assert out.read_bytes().startswith(b"%PDF") and out.stat().st_size > 0
    assert printer.html == html and "INDICATIVA" in printer.html


@pytest.mark.parametrize("payload", [b"", b"no es un pdf"])
def test_print_pdf_rejects_empty_or_invalid_output(tmp_path, payload):
    with pytest.raises(PdfGenerationError):
        print_pdf("<html></html>", tmp_path / "x.pdf", FakePrinter(payload))


def test_playwright_printer_explains_what_is_missing_without_installing(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def no_playwright(name, *args, **kwargs):
        if name.startswith("playwright"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_playwright)
    with pytest.raises(PdfUnavailableError) as info:
        PlaywrightPrinter().print_pdf("<html></html>", tmp_path / "x.pdf")
    assert "playwright install chromium" in str(info.value)


def test_browser_printer_without_a_browser_says_so(tmp_path):
    printer = BrowserCliPrinter()
    printer._browser = None
    with pytest.raises(PdfUnavailableError):
        printer.print_pdf("<html></html>", tmp_path / "x.pdf")


@pytest.mark.skipif(find_browser() is None, reason="no hay Edge, Chrome ni Chromium instalado")
def test_real_pdf_with_a_local_chromium_has_accents_and_symbols(tmp_path):
    content, images = build("cruza_dia_4", "weekly")
    html = render_report_html(content, images).html
    out = print_pdf(html, tmp_path / "informe.pdf", BrowserCliPrinter())
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 20_000
