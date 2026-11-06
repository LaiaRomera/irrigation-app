"""Informe de riego: contenido, texto (LLM o plantilla), validación de cifras, gráficas, HTML y PDF.

Flujo: `EngineResult` -> `content.build_report_content` -> `llm.generate_text` (con `validation`) ->
`render.render_report_html` (+ `charts`) -> `pdf.print_pdf`. Ver `README.md`.
"""
