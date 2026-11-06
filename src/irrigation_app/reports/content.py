"""Contenido estructurado del informe: todo lo que el informe muestra, sacado del `EngineResult`.

`build_report_content` copia las cifras del motor sin recalcular ninguna. El informe diario detalla el primer
día del plan; el semanal añade los otros seis resumidos (decisión, dosis en mm y lluvia prevista). Esta
estructura es lo que se guarda como contenido del informe y lo que se pasa al LLM y a la plantilla HTML.

`ReportContent.figures()` devuelve las cifras que un texto puede usar: es la lista que comprueba
`validation.py`.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict

from irrigation_app.irrigation_engine.result import DayPlan, Decision, EngineResult, ValidationStatus
from irrigation_app.reports.metrics import HorizonError
from irrigation_app.reports.validation import AllowedFigures

ReportType = Literal["daily", "weekly"]

DECISION_LABELS: dict[str, str] = {
    "irrigate": "Regar",
    "postpone": "Aplazar el riego por lluvia prevista",
    "do_not_irrigate": "No regar",
    "insufficient_data": "Datos insuficientes",
}
VALIDATION_LABELS: dict[str, str] = {
    "validated": "Validada",
    "provisional": "Provisional",
    "insufficient_data": "Datos insuficientes",
}
REASON_LABELS: dict[str, str] = {
    "below_threshold": "la humedad media de la zona radicular sigue por encima del umbral de riego",
    "threshold_crossed": "la humedad media de la zona radicular llega al umbral de riego",
    "rain_forecast": "hay lluvia prevista suficiente para aplazar el riego",
    "out_of_season": "es época de reposo del cultivo, fuera de la campaña de riego",
    "after_irrigation": "ya hay un riego anterior en el plan de estos días",
    "no_probe_prediction": "ninguna sonda de la zona radicular tiene predicción ese día",
    "missing_crop": "falta el cultivo de la parcela",
    "missing_soil_texture": "falta la textura del suelo",
    "missing_anchors": "faltan los anclajes de la escala del sensor",
}
# Limitaciones que el informe destaca siempre (las pidió el handoff del motor del 5 de noviembre de 2026)
HIGHLIGHTED_LIMITATIONS = (
    "assumed_ground_cover", "assumed_crop_stage", "assumed_replenishment_interval",
    "upper_anchor_above_observed_max", "localized_irrigation", "out_of_season", "relative_scale_indicative",
)

UNCERTAINTY_NOTE = (
    "El día en que la humedad cruzará el umbral de riego no es exacto. La predicción de humedad tiene un error "
    "típico que crece con el horizonte (ver la tabla de error) y, en las pruebas del modelo, acertar el día exacto "
    "fue poco frecuente. Hay que leerlo como un rango de días, y la lectura real de la sonda es la que decide el "
    "momento del riego."
)
DOSE_NOTE = (
    "Los milímetros de la dosis son sobre la franja mojada por el goteo, no sobre toda la superficie, y el motor "
    "no da duración del riego porque no se conoce el caudal."
)

_MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre")


def format_number(value: float | int | None, decimals: int = 1) -> str:
    """Número con coma decimal («17,6»). Un valor desconocido se muestra como un guion."""
    if value is None:
        return "—"
    return f"{value:.{decimals}f}".replace(".", ",")


def format_date(value: dt.date | None) -> str:
    """Fecha larga en español («25 de agosto de 2026»)."""
    if value is None:
        return "—"
    return f"{value.day} de {_MONTHS[value.month - 1]} de {value.year}"


class DayDetail(BaseModel):
    """El primer día del plan, con todo el detalle."""

    model_config = ConfigDict(frozen=True)

    day: int
    date: dt.date
    decision: Decision
    decision_label: str
    reasons: tuple[str, ...] = ()  # frases en español
    moisture_mean: float | None = None
    depletion_pct: int | None = None  # fracción gastada ×100, redondeada
    p: float | None = None
    stage: str | None = None
    kc: float | None = None
    eto_mm: float | None = None
    etc_mm: float | None = None
    rain_mm: float | None = None
    net_dose_mm: float | None = None
    gross_dose_mm: float | None = None
    dose_limit: str | None = None


class DaySummary(BaseModel):
    """Un día del informe semanal, resumido."""

    model_config = ConfigDict(frozen=True)

    day: int
    date: dt.date
    decision: Decision
    decision_label: str
    net_dose_mm: float | None = None
    gross_dose_mm: float | None = None
    rain_mm: float | None = None


class IrrigationOutlook(BaseModel):
    """Cuándo y cuánto se regaría según el plan: orientativo, con rango (no es un día exacto)."""

    model_config = ConfigDict(frozen=True)

    day: int
    date: dt.date
    day_min: int  # primer día de riego entre el caso base y los de sensibilidad
    day_max: int
    net_dose_mm: float | None = None
    gross_dose_mm: float | None = None
    net_dose_min_mm: float | None = None
    net_dose_max_mm: float | None = None
    cases_without_irrigation: int = 0  # casos de sensibilidad (base incluido) que no riegan en 7 días
    cases_total: int = 0


class LimitationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    highlighted: bool = False
    degrades_validation: bool = False


class ReportText(BaseModel):
    """Texto redactado: resumen, motivo y aviso. Lo pone el LLM o la plantilla de respaldo."""

    model_config = ConfigDict(frozen=True)

    summary: str
    reason: str
    warning: str
    generator: Literal["llm", "template"] = "template"
    generator_version: str = "template-1"
    fallback_reason: str | None = None  # por qué se usó la plantilla en lugar del LLM


class ReportContent(BaseModel):
    """Todo lo que muestra un informe. Las cifras salen del `EngineResult`, nunca de otra parte."""

    model_config = ConfigDict(frozen=True)

    report_type: ReportType
    plot_label: str
    origin_date: dt.date
    period_start: dt.date
    period_end: dt.date
    today: dt.date | None = None  # fecha en que se consulta; si es posterior al origen, el informe es antiguo
    decision: Decision
    decision_label: str
    validation_status: ValidationStatus
    validation_label: str
    indicative: bool
    indicative_note: str
    first_day: DayDetail
    other_days: tuple[DaySummary, ...] = ()  # solo en el semanal
    outlook: IrrigationOutlook | None = None
    typical_error: tuple[HorizonError, ...] = ()
    limitations: tuple[LimitationView, ...] = ()
    dose_on_wetted_strip: bool = False
    replenishment_interval_days: int | None = None
    method_version: str = ""
    uncertainty_note: str = UNCERTAINTY_NOTE
    dose_note: str = DOSE_NOTE
    text: ReportText | None = None

    @property
    def is_stale(self) -> bool:
        """True si el informe es de un día anterior al de la consulta: nunca se presenta como actual."""
        return self.today is not None and self.origin_date < self.today

    @property
    def has_dose(self) -> bool:
        return self.first_day.net_dose_mm is not None or (self.outlook is not None and self.outlook.net_dose_mm is not None)

    def with_text(self, text: ReportText) -> "ReportContent":
        return self.model_copy(update={"text": text})

    def figures(self) -> AllowedFigures:
        """Las cifras que un texto del informe puede citar: las que el propio informe muestra."""
        allowed = AllowedFigures()
        dates = [self.origin_date, self.period_start, self.period_end, self.first_day.date]
        dates += [d.date for d in self.other_days]
        if self.outlook:
            dates.append(self.outlook.date)
        for d in dates:
            allowed.add_all([d.day, d.month, d.year])

        fd = self.first_day
        allowed.add_all([fd.day, fd.moisture_mean, fd.p, fd.kc])
        allowed.add(fd.depletion_pct, "%")
        allowed.add_all([fd.eto_mm, fd.etc_mm, fd.rain_mm, fd.net_dose_mm, fd.gross_dose_mm], "mm")
        for d in self.other_days:
            allowed.add(d.day)
            allowed.add_all([d.net_dose_mm, d.gross_dose_mm, d.rain_mm], "mm")
        if self.outlook:
            o = self.outlook
            allowed.add_all([o.day, o.day_min, o.day_max, o.cases_without_irrigation, o.cases_total])
            allowed.add_all([o.net_dose_mm, o.gross_dose_mm, o.net_dose_min_mm, o.net_dose_max_mm], "mm")
        for e in self.typical_error:
            allowed.add(e.horizon_days)
            allowed.add_all([e.mae, e.rmse], "puntos")
            allowed.add_all([e.mae, e.rmse])
        allowed.add(self.replenishment_interval_days)
        allowed.add(self.replenishment_interval_days, "días")
        for text in [self.plot_label, self.indicative_note, self.uncertainty_note, self.dose_note] + [l.message for l in self.limitations]:
            allowed.add_text(text)
        return allowed

    def llm_facts(self) -> dict[str, object]:
        """Los datos que se le pasan al LLM (sin su propio texto): son los únicos de los que puede sacar cifras."""
        return self.model_dump(mode="json", exclude={"text", "today"})


def _reason_phrases(codes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(REASON_LABELS.get(code, code) for code in codes)


def _detail(day: DayPlan) -> DayDetail:
    return DayDetail(
        day=day.day, date=day.date, decision=day.decision, decision_label=DECISION_LABELS[day.decision],
        reasons=_reason_phrases(day.reasons), moisture_mean=_round(day.moisture_mean, 1),
        depletion_pct=None if day.depletion is None else round(day.depletion * 100),
        p=_round(day.p, 2), stage=day.stage, kc=_round(day.kc, 2), eto_mm=_round(day.eto_mm, 1), etc_mm=_round(day.etc_mm, 1),
        rain_mm=_round(day.rain_mm, 1), net_dose_mm=_round(day.net_dose_mm, 1), gross_dose_mm=_round(day.gross_dose_mm, 1),
        dose_limit=day.dose_limit,
    )


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _summary(day: DayPlan) -> DaySummary:
    return DaySummary(
        day=day.day, date=day.date, decision=day.decision, decision_label=DECISION_LABELS[day.decision],
        net_dose_mm=_round(day.net_dose_mm, 1), gross_dose_mm=_round(day.gross_dose_mm, 1), rain_mm=_round(day.rain_mm, 1),
    )


def _outlook(result: EngineResult) -> IrrigationOutlook | None:
    if result.irrigation_day is None or result.irrigation_date is None:
        return None
    days = [result.irrigation_day]
    sens = result.sensitivity
    if sens is not None:
        days += [c.irrigation_day for c in (sens.base, *sens.cases) if c.irrigation_day is not None]
    return IrrigationOutlook(
        day=result.irrigation_day, date=result.irrigation_date, day_min=min(days), day_max=max(days),
        net_dose_mm=_round(result.net_dose_mm, 1), gross_dose_mm=_round(result.gross_dose_mm, 1),
        net_dose_min_mm=_round(sens.net_dose_min_mm, 1) if sens else None,
        net_dose_max_mm=_round(sens.net_dose_max_mm, 1) if sens else None,
        cases_without_irrigation=sens.cases_without_irrigation if sens else 0,
        cases_total=(1 + len(sens.cases)) if sens else 0,
    )


# Las cifras del informe se guardan ya redondeadas a lo que se muestra (1 decimal en mm y en el índice, 2 en Kc y p):
# así el texto, la plantilla y la validación hablan del mismo número y no de 5,56 frente a «5,6».
def build_report_content(
    result: EngineResult,
    *,
    report_type: ReportType = "daily",
    plot_label: str = "",
    typical_error: tuple[HorizonError, ...] = (),
    today: dt.date | None = None,
) -> ReportContent:
    """Monta el contenido del informe a partir del resultado del motor (sin recalcular nada)."""
    first, rest = result.plan[0], result.plan[1:]
    limitations = tuple(
        LimitationView(
            code=lim.code, message=lim.message, highlighted=lim.code in HIGHLIGHTED_LIMITATIONS,
            degrades_validation=lim.degrades_validation,
        )
        for lim in result.limitations
    )
    return ReportContent(
        report_type=report_type, plot_label=plot_label, origin_date=result.origin_date,
        period_start=result.period_start, period_end=result.period_end, today=today,
        decision=result.decision, decision_label=DECISION_LABELS[result.decision],
        validation_status=result.validation_status, validation_label=VALIDATION_LABELS[result.validation_status],
        indicative=result.indicative, indicative_note=result.indicative_note,
        first_day=_detail(first),
        other_days=tuple(_summary(d) for d in rest) if report_type == "weekly" else (),
        outlook=_outlook(result), typical_error=typical_error, limitations=limitations,
        dose_on_wetted_strip=result.dose_on_wetted_strip,
        replenishment_interval_days=result.replenishment_interval_days, method_version=result.method_version,
    )
