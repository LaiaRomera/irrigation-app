"""Relleno de la plantilla HTML del informe con Jinja2 (tarea 7.3).

El código rellena la plantilla; el LLM nunca escribe HTML. La plantilla se busca así:
1. `template_dir` si se pasa, o la variable de entorno `AGRODEVICES_TEMPLATE_DIR`, con un `index.html` dentro:
   es la plantilla de Agrodevices (documento interno, **no está en el repositorio**).
2. Si no existe, la plantilla mínima propia `templates/informe_minimo.html`, y el resultado lo dice
   (`template_source == "builtin_minimal"`).

Las variables que recibe cualquier plantilla están en `reports/README.md`.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from irrigation_app.irrigation_engine.inputs import EngineInput
from irrigation_app.irrigation_engine.result import EngineResult
from irrigation_app.reports import charts
from irrigation_app.reports.content import ReportContent, format_date, format_number

TEMPLATE_DIR_ENV = "AGRODEVICES_TEMPLATE_DIR"
TEMPLATE_ENTRY = "index.html"  # el paquete MASTER_HTML de Agrodevices tiene un index.html
BUILTIN_DIR = Path(__file__).parent / "templates"
BUILTIN_ENTRY = "informe_minimo.html"

_STAGES = {"ini": "inicial", "dev": "desarrollo", "mid": "media", "late": "final"}


@dataclass(frozen=True)
class RenderedReport:
    html: str
    template_source: Literal["agrodevices", "builtin_minimal"]
    template_note: str = ""  # por qué se usó la plantilla mínima, o un aviso sobre la de Agrodevices
    base_dir: Path | None = None  # carpeta de la plantilla externa: desde ahí se resuelven sus recursos


def resolve_template_dir(template_dir: Path | str | None = None, environ: Mapping[str, str] | None = None) -> Path | None:
    """La carpeta de la plantilla externa, si se indicó y tiene `index.html`; si no, `None`."""
    raw = template_dir if template_dir is not None else (environ if environ is not None else os.environ).get(TEMPLATE_DIR_ENV)
    if not raw:
        return None
    path = Path(raw)
    return path if (path / TEMPLATE_ENTRY).is_file() else None


def _environment(directory: Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(directory)), autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True,
    )
    env.filters["num"] = lambda value, decimals=1: format_number(value, decimals)
    env.filters["date_es"] = format_date
    env.filters["stage_es"] = lambda value: _STAGES.get(value, value) if value else "—"
    return env


def make_charts(
    result: EngineResult, engine_input: EngineInput | None, content: ReportContent
) -> dict[str, str | None]:
    """Las tres gráficas como `data:` URI. El plan solo se dibuja en el semanal; el error si hay métricas."""
    data = charts.chart_data(result, engine_input)
    return {
        "moisture": charts.to_data_uri(charts.moisture_chart(data)),
        "plan": charts.to_data_uri(charts.plan_chart(data)) if content.report_type == "weekly" else None,
        "error": charts.to_data_uri(charts.error_chart(content.typical_error)) if content.typical_error else None,
    }


def render_report_html(
    content: ReportContent,
    chart_images: Mapping[str, str | None] | None = None,
    *,
    template_dir: Path | str | None = None,
    generated_at: dt.datetime | None = None,
) -> RenderedReport:
    """Rellena la plantilla con el contenido (que ya debe traer su `text`). Devuelve el HTML y de dónde salió."""
    external = resolve_template_dir(template_dir)
    note = ""
    if external is None:
        directory, entry, source = BUILTIN_DIR, BUILTIN_ENTRY, "builtin_minimal"
        if template_dir is not None or os.environ.get(TEMPLATE_DIR_ENV):
            note = "La plantilla de Agrodevices no está accesible en la ruta indicada: se usa la plantilla mínima."
    else:
        directory, entry, source = external, TEMPLATE_ENTRY, "agrodevices"
    template = _environment(directory).get_template(entry)
    context = {
        "c": content, "text": content.text, "charts": {"moisture": None, "plan": None, "error": None, **(chart_images or {})},
        "generated_at": generated_at or dt.datetime.now(dt.timezone.utc),
    }
    html = template.render(**context)
    if source == "agrodevices":
        if "{{" not in (external / TEMPLATE_ENTRY).read_text(encoding="utf-8", errors="ignore"):
            note = "La plantilla de Agrodevices no tiene marcas Jinja ({{ ... }}): no se rellenó ningún campo."
        html = re.sub(r"(<head[^>]*>)", rf'\1<base href="{external.resolve().as_uri()}/">', html, count=1, flags=re.IGNORECASE)
    return RenderedReport(html=html, template_source=source, template_note=note, base_dir=external)
