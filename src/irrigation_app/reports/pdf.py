"""Impresión del informe a PDF con Chromium (tarea 7.3, decisión D10).

El PDF sale siempre del HTML ya relleno (`render.py`); no se reconstruye con ReportLab ni WeasyPrint.
Hay dos formas de imprimir, que comparten la interfaz `PdfPrinter`:
- `PlaywrightPrinter`: la prevista (Playwright + Chromium). Necesita `pip install playwright` y
  `python -m playwright install chromium`; **no se instala sola** (hace falta el permiso de Laia).
- `BrowserCliPrinter`: usa un Edge o Chrome ya instalado, en modo sin ventana, sin descargar nada.
  Es el mismo motor de impresión (Chromium), así que el resultado es equivalente.

Un PDF no se guarda en la base de datos: se genera al pedirlo, a partir del contenido guardado del informe.
Los errores no enseñan rutas ni trazas: `PdfUnavailableError` y `PdfGenerationError` llevan mensajes seguros.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path

PDF_TIMEOUT_S = 90
_BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


class PdfUnavailableError(RuntimeError):
    """No hay forma de imprimir a PDF en este equipo. El mensaje dice qué hace falta."""


class PdfGenerationError(RuntimeError):
    """La impresión falló. El mensaje es genérico: nunca incluye rutas ni salida del navegador."""


class PdfPrinter(ABC):
    @abstractmethod
    def print_pdf(self, html: str, out_path: Path) -> None:
        """Escribe el PDF de `html` en `out_path`."""


class PlaywrightPrinter(PdfPrinter):
    """Playwright con su Chromium. Se importa al usarlo para que el resto funcione sin instalarlo."""

    def print_pdf(self, html: str, out_path: Path) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise PdfUnavailableError(
                "Playwright no está instalado. Hace falta: pip install playwright y "
                "python -m playwright install chromium."
            ) from None
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            source = Path(tmp) / "informe.html"
            source.write_text(html, encoding="utf-8")
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch()
                    page = browser.new_page()
                    page.goto(source.as_uri())
                    page.pdf(path=str(out_path), format="A4", print_background=True, prefer_css_page_size=True)
                    browser.close()
            except Exception:  # noqa: BLE001 - se oculta el detalle a propósito
                raise PdfGenerationError("No se pudo generar el PDF con Playwright.") from None


def find_browser() -> str | None:
    """Un Edge o Chrome instalado (variable `PDF_BROWSER_PATH` si se quiere forzar uno), o `None`."""
    forced = os.environ.get("PDF_BROWSER_PATH")
    candidates = ((forced,) if forced else ()) + _BROWSER_CANDIDATES
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return None


class BrowserCliPrinter(PdfPrinter):
    """Imprime con un navegador Chromium ya instalado, sin ventana y sin descargar nada."""

    def __init__(self, browser_path: str | None = None):
        self._browser = browser_path or find_browser()

    def print_pdf(self, html: str, out_path: Path) -> None:
        if not self._browser:
            raise PdfUnavailableError("No hay Edge, Chrome ni Chromium instalado para imprimir el PDF.")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            source = Path(tmp) / "informe.html"
            source.write_text(html, encoding="utf-8")
            command = [
                self._browser, "--headless", "--disable-gpu", "--no-pdf-header-footer", "--no-first-run",
                f"--user-data-dir={Path(tmp) / 'profile'}", f"--print-to-pdf={out_path}", source.as_uri(),
            ]
            out_path.unlink(missing_ok=True)  # así un PDF viejo no se confunde con el nuevo
            try:
                subprocess.run(command, check=True, capture_output=True, timeout=PDF_TIMEOUT_S)
            except (subprocess.SubprocessError, OSError):
                raise PdfGenerationError("No se pudo generar el PDF con el navegador local.") from None
            _wait_until_written(out_path)


def _wait_until_written(path: Path, timeout_s: float = 30.0) -> None:
    """Edge puede devolver el control antes de terminar de escribir: se espera a que el archivo exista y deje de crecer."""
    deadline = time.monotonic() + timeout_s
    last_size = -1
    while time.monotonic() < deadline:
        size = path.stat().st_size if path.is_file() else 0
        if size > 0 and size == last_size:
            return
        last_size = size
        time.sleep(0.5)


def default_printer() -> PdfPrinter:
    """Playwright si está instalado; si no, un Chromium local; si no hay ninguno, `PdfUnavailableError`."""
    try:
        import playwright  # noqa: F401

        return PlaywrightPrinter()
    except ImportError:
        pass
    if find_browser():
        return BrowserCliPrinter()
    raise PdfUnavailableError(
        "No hay forma de imprimir el PDF: instale Playwright (pip install playwright y "
        "python -m playwright install chromium) o un navegador Edge/Chrome."
    )


def print_pdf(html: str, out_path: Path | str, printer: PdfPrinter | None = None) -> Path:
    """Genera el PDF de un HTML ya relleno y comprueba que el archivo es un PDF no vacío."""
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    (printer or default_printer()).print_pdf(html, target)
    if not target.is_file() or target.stat().st_size == 0 or not target.read_bytes().startswith(b"%PDF"):
        raise PdfGenerationError("El PDF generado está vacío o no es válido.")
    return target
