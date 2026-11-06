"""Redacción del texto del informe (tarea 7.2): plantilla de respaldo y LLM con interfaz de proveedor abstracta.

- `TemplateGenerator`: texto determinista a partir del contenido; siempre disponible, sin red ni claves.
- `LlmGenerator`: pide al LLM un JSON con `summary`, `reason` y `warning`. El proveedor concreto es un
  `LlmClient` (aún no se ha elegido: tarea 0.4); aquí solo hay una interfaz y un cliente simulado para pruebas.
- `generate_text`: orquesta. Intenta el LLM; si no hay cliente, falla la llamada, la respuesta no tiene la
  estructura o alguna cifra no coincide con el motor, usa la plantilla y registra el motivo.

El LLM no escribe HTML ni busca en internet. Los motivos de fallo son códigos cortos: nunca incluyen claves,
rutas ni trazas.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from irrigation_app.reports.content import (
    ReportContent,
    ReportText,
    format_date,
    format_number,
)
from irrigation_app.reports.validation import validate_fields

logger = logging.getLogger(__name__)

PROMPT_VERSION = "agronomist_v1"
PROMPT_PATH = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"
TEMPLATE_VERSION = "template-1"
DEFAULT_TIMEOUT_S = 30.0
REQUIRED_FIELDS = ("summary", "reason", "warning")
MAX_FIELD_CHARS = 1200

_STAGE_LABELS = {"ini": "inicial", "dev": "de desarrollo", "mid": "media", "late": "final"}
_HTML = re.compile(r"<\s*/?\s*[a-zA-Z!]")


class TextGenerationError(Exception):
    """Fallo al redactar con el LLM. `reason` es un código corto, seguro para registrar y mostrar."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class TextGenerator(ABC):
    """Algo que redacta `summary`, `reason` y `warning` a partir del contenido del informe."""

    @property
    @abstractmethod
    def version(self) -> str: ...

    @abstractmethod
    def generate(self, content: ReportContent) -> ReportText: ...


# --- Plantilla de respaldo ----------------------------------------------------------------------
class TemplateGenerator(TextGenerator):
    """Texto determinista. Solo usa cifras del contenido, con el mismo formato que el informe."""

    @property
    def version(self) -> str:
        return TEMPLATE_VERSION

    def generate(self, content: ReportContent) -> ReportText:
        return ReportText(
            summary=self._summary(content), reason=self._reason(content), warning=self._warning(content),
            generator="template", generator_version=self.version,
        )

    @staticmethod
    def _outlook_sentence(content: ReportContent) -> str:
        o = content.outlook
        if o is None:
            return ""
        when = (f"hacia el día {o.day} ({format_date(o.date)})" if o.day_min == o.day_max
                else f"hacia el día {o.day} ({format_date(o.date)}), con un rango entre el día {o.day_min} "
                     f"y el día {o.day_max}")
        dose = ""
        if o.net_dose_mm is not None:
            dose = (f" La dosis orientativa sería de {format_number(o.net_dose_mm)} mm netos"
                    f" ({format_number(o.gross_dose_mm)} mm brutos)"
                    + (" sobre la franja mojada." if content.dose_on_wetted_strip else "."))
        return (f" Según el plan, el umbral de riego se alcanzaría {when}; no es un día exacto y la lectura "
                f"real de la sonda decide.{dose}")

    def _summary(self, c: ReportContent) -> str:
        d = c.first_day
        head = f"{c.plot_label or 'La parcela'}, día {d.day} ({format_date(d.date)}; cálculo del {format_date(c.origin_date)})"
        if c.decision == "insufficient_data":
            why = "; ".join(d.reasons) if d.reasons else "no hay datos suficientes"
            return f"{head}: datos insuficientes para recomendar una dosis ({why})."
        if c.decision == "irrigate":
            dose = f"dosis neta de {format_number(d.net_dose_mm)} mm y bruta de {format_number(d.gross_dose_mm)} mm"
            strip = " sobre la franja mojada" if c.dose_on_wetted_strip else ""
            return f"{head}: regar, {dose}{strip}. Recomendación indicativa."
        if c.decision == "postpone":
            rain = f" ({format_number(d.rain_mm)} mm)" if d.rain_mm is not None else ""
            return (f"{head}: aplazar el riego, porque se espera lluvia{rain}. "
                    "Recomendación indicativa." + self._outlook_sentence(c))
        moisture = (f" La humedad media prevista de la zona radicular es de {format_number(d.moisture_mean)} "
                    "sobre 100." if d.moisture_mean is not None else "")
        return f"{head}: no regar.{moisture} Recomendación indicativa." + self._outlook_sentence(c)

    def _reason(self, c: ReportContent) -> str:
        d = c.first_day
        parts: list[str] = []
        if d.reasons:
            parts.append(d.reasons[0][:1].upper() + d.reasons[0][1:] + (" y " + "; ".join(d.reasons[1:]) if len(d.reasons) > 1 else "") + ".")
        calc: list[str] = []
        if d.stage is not None and d.kc is not None:
            calc.append(f"el cultivo está en etapa {_STAGE_LABELS.get(d.stage, d.stage)} (Kc {format_number(d.kc, 2)})")
        if d.eto_mm is not None:
            calc.append(f"la ET₀ prevista es de {format_number(d.eto_mm)} mm")
        if d.etc_mm is not None:
            calc.append(f"la ETc de {format_number(d.etc_mm)} mm")
        if d.depletion_pct is not None:
            calc.append(f"la fracción de agua útil gastada es del {d.depletion_pct} %")
        if calc:
            parts.append("Para el cálculo: " + ", ".join(calc) + ".")
        if d.rain_mm:
            parts.append(f"Lluvia prevista: {format_number(d.rain_mm)} mm.")
        return " ".join(parts) or "El motor no aporta más detalle para este día."

    def _warning(self, c: ReportContent) -> str:
        parts = [c.indicative_note if c.indicative else "", c.uncertainty_note if c.outlook or c.decision == "do_not_irrigate" else ""]
        codes = {lim.code for lim in c.limitations}
        assumed = [label for code, label in (
            ("assumed_ground_cover", "la cubierta vegetal entre las filas"),
            ("assumed_crop_stage", "la fecha de inicio del ciclo del cultivo"),
            ("assumed_replenishment_interval", "el intervalo de reposición del riego"),
        ) if code in codes]
        if assumed:
            interval = ""
            if "assumed_replenishment_interval" in codes and c.replenishment_interval_days is not None:
                interval = f" (se supone un intervalo de {c.replenishment_interval_days} días)"
            parts.append("Supuestos sin confirmar: " + ", ".join(assumed) + interval + ".")
        if "upper_anchor_above_observed_max" in codes:
            parts.append("El suelo lleno real de la sonda queda por debajo del anclaje superior usado, "
                         "así que el momento de cruce del umbral puede adelantarse o retrasarse.")
        if c.outlook is None and c.decision == "insufficient_data":
            parts.append("Hay que revisar los datos que faltan antes de usar esta recomendación.")
        return " ".join(p for p in parts if p)


# --- LLM -----------------------------------------------------------------------------------------
class LlmClient(ABC):
    """Interfaz de un proveedor de LLM. Cada proveedor (aún sin elegir, tarea 0.4) implementa `complete`."""

    provider: str = "unknown"
    model: str = "unknown"

    @abstractmethod
    def complete(self, *, system: str, user: str, timeout_s: float) -> str:
        """Devuelve el texto de la respuesta (se espera un JSON). Lanza cualquier excepción si falla."""


class FakeLlmClient(LlmClient):
    """Cliente simulado para pruebas: devuelve una respuesta fija, calculada o lanza un error. No usa red."""

    provider = "fake"
    model = "fake-1"

    def __init__(self, response: str | Callable[[str, str], str] | BaseException):
        self._response = response
        self.calls: list[dict[str, object]] = []

    def complete(self, *, system: str, user: str, timeout_s: float) -> str:
        self.calls.append({"system": system, "user": user, "timeout_s": timeout_s})
        if isinstance(self._response, BaseException):
            raise self._response
        return self._response(system, user) if callable(self._response) else self._response


def load_prompt(path: Path = PROMPT_PATH) -> str:
    """Lee el prompt versionado. El archivo empieza con un comentario HTML que no se envía."""
    text = path.read_text(encoding="utf-8")
    return re.sub(r"^\s*<!--.*?-->\s*", "", text, flags=re.DOTALL)


def parse_llm_json(raw: str) -> dict[str, str]:
    """Extrae `summary`, `reason` y `warning` de la respuesta. Lanza `TextGenerationError` si falta la estructura."""
    text = raw.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        raise TextGenerationError("invalid_json") from None
    if not isinstance(data, dict):
        raise TextGenerationError("invalid_json")
    fields: dict[str, str] = {}
    missing = []
    for name in REQUIRED_FIELDS:
        value = data.get(name)
        if not isinstance(value, str) or not value.strip():
            missing.append(name)
        else:
            fields[name] = value.strip()
    if missing:
        raise TextGenerationError("missing_fields:" + ",".join(missing))
    for name, value in fields.items():
        if _HTML.search(value):
            raise TextGenerationError(f"html_in_{name}")
        if len(value) > MAX_FIELD_CHARS:
            raise TextGenerationError(f"too_long:{name}")
    return fields


class LlmGenerator(TextGenerator):
    """Redacta con un LLM externo. No valida las cifras: eso lo hace `generate_text` para cualquier generador."""

    def __init__(self, client: LlmClient, *, timeout_s: float = DEFAULT_TIMEOUT_S, system_prompt: str | None = None):
        self._client = client
        self._timeout_s = timeout_s
        self._system = system_prompt if system_prompt is not None else load_prompt()

    @property
    def version(self) -> str:
        return f"{self._client.provider}/{self._client.model}/{PROMPT_VERSION}"

    def generate(self, content: ReportContent) -> ReportText:
        user = json.dumps(content.llm_facts(), ensure_ascii=False, default=str)
        try:
            raw = self._client.complete(system=self._system, user=user, timeout_s=self._timeout_s)
        except Exception as exc:  # noqa: BLE001 - cualquier fallo del proveedor se convierte en un código seguro
            raise TextGenerationError(f"api_error:{type(exc).__name__}") from None
        fields = parse_llm_json(raw)
        return ReportText(**fields, generator="llm", generator_version=self.version)


def generate_text(
    content: ReportContent, generator: TextGenerator | None = None, fallback: TextGenerator | None = None
) -> ReportText:
    """Redacta el texto. Con LLM si se puede y las cifras coinciden; si no, con la plantilla y el motivo."""
    fallback = fallback or TemplateGenerator()
    if generator is None:
        return _fallback(content, fallback, "no_llm_configured")
    try:
        text = generator.generate(content)
    except TextGenerationError as exc:
        return _fallback(content, fallback, exc.reason)
    except Exception as exc:  # noqa: BLE001
        return _fallback(content, fallback, f"unexpected_error:{type(exc).__name__}")
    check = validate_fields(
        {"summary": text.summary, "reason": text.reason, "warning": text.warning}, content.figures()
    )
    if not check.ok:
        return _fallback(content, fallback, "validation_failed: " + check.reason)
    return text


def _fallback(content: ReportContent, fallback: TextGenerator, reason: str) -> ReportText:
    logger.warning("Informe con plantilla de respaldo: %s", reason)
    return fallback.generate(content).model_copy(update={"fallback_reason": reason})
