"""Genera informes de ejemplo (HTML y, si hay navegador, PDF) con resultados sintéticos del motor.

Uso (desde la raíz del proyecto):
    python tests/fixtures/make_sample_reports.py [--out data/reports_examples] [--no-pdf]

Usa siempre la plantilla de respaldo para el texto (sin LLM ni claves). La salida va a `data/`, que git ignora.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "src", ROOT / "tests"):
    sys.path.insert(0, str(path))

from fixtures.engine_samples import PLOT_LABEL, sample_inputs, typical_error  # noqa: E402

from irrigation_app.irrigation_engine import run_engine  # noqa: E402
from irrigation_app.reports.content import build_report_content  # noqa: E402
from irrigation_app.reports.llm import generate_text  # noqa: E402
from irrigation_app.reports.pdf import PdfUnavailableError, print_pdf  # noqa: E402
from irrigation_app.reports.render import make_charts, render_report_html  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "reports_examples")
    parser.add_argument("--no-pdf", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    for name, engine_input in sample_inputs().items():
        result = run_engine(engine_input)
        for report_type in ("daily", "weekly"):
            content = build_report_content(
                result, report_type=report_type, plot_label=PLOT_LABEL, typical_error=typical_error()
            )
            content = content.with_text(generate_text(content))
            rendered = render_report_html(content, make_charts(result, engine_input, content))
            stem = args.out / f"{name}_{report_type}"
            stem.with_suffix(".html").write_text(rendered.html, encoding="utf-8")
            line = f"{stem.name}.html ({rendered.template_source})"
            if not args.no_pdf:
                try:
                    print_pdf(rendered.html, stem.with_suffix(".pdf"))
                    line += " + PDF"
                except PdfUnavailableError as exc:
                    line += f" (sin PDF: {exc})"
            print(line)


if __name__ == "__main__":
    main()
