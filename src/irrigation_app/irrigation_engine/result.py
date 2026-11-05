"""Salida del motor de riego (contrato C2): `EngineResult`.

El plan de 7 días lleva una decisión por día, dosis neta y bruta en mm (el volumen en m³ solo si se conoce la
superficie; no existe ningún campo de duración, porque sin caudal no se puede dar), el estado de validación, los
factores usados con su origen, las limitaciones y el análisis de sensibilidad. La recomendación siempre es
**indicativa** (escala relativa del sensor). Un valor desconocido es `None`.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Decision = Literal["irrigate", "postpone", "do_not_irrigate", "insufficient_data"]
ValidationStatus = Literal["validated", "provisional", "insufficient_data"]
DoseCriterion = Literal["replenishment", "field_capacity"]
# replenishment: manda la reposición de la ETc; depletion: manda el agotamiento actual; raw_cap: manda el tope RAW;
# no_etc: sin ETc prevista, manda el menor de agotamiento y tope; field_capacity: criterio de riego no localizado
DoseLimit = Literal["replenishment", "depletion", "raw_cap", "no_etc", "field_capacity"]

METHOD_VERSION = "fao56-engine-2"
PARAMETER_SET_VERSION = "fao56-tables-2026-11-05"


class Factor(BaseModel):
    """Un valor usado en el cálculo, con su unidad, su origen y si es de tabla, dato del usuario o supuesto."""

    model_config = ConfigDict(frozen=True)

    name: str
    value: float | str | None
    unit: str = ""
    source: str
    status: Literal["verified", "assumption", "provided", "derived"]
    note: str = ""


class Limitation(BaseModel):
    """Un supuesto, aviso o límite del cálculo. `code` es estable; `message` es el texto para el informe."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    params: dict[str, object] = Field(default_factory=dict)
    degrades_validation: bool = False  # si es True, el resultado no puede ser `validated`


class DayPlan(BaseModel):
    """Un día del plan. Los campos desconocidos o que no aplican son `None`."""

    model_config = ConfigDict(frozen=True)

    day: int = Field(ge=1, le=7)  # = horizonte de la predicción
    date: dt.date
    decision: Decision
    reasons: tuple[str, ...] = ()  # códigos: below_threshold, threshold_crossed, rain_forecast, out_of_season, ...
    moisture_mean: float | None = None  # índice 0 a 100, media de las sondas de la zona radicular
    probes_used_cm: tuple[int, ...] = ()
    probes_missing_cm: tuple[int, ...] = ()
    depletion: float | None = None  # fracción de agua útil gastada, entre 0 y 1
    depletion_clamped: bool = False  # True si la fracción real salía de 0 a 1 y se recortó
    p: float | None = None  # fracción de agotamiento admisible de ese día
    stage: str | None = None
    kc: float | None = None
    eto_mm: float | None = None
    etc_mm: float | None = None
    rain_mm: float | None = None
    net_dose_mm: float | None = None
    gross_dose_mm: float | None = None
    volume_m3: float | None = None  # solo si se conoce la superficie
    # Cómo se obtuvo la dosis del día de riego (solo en `irrigate`)
    depletion_mm: float | None = None  # agotamiento actual D × TAW × Zr: la dosis neta nunca lo supera
    raw_cap_mm: float | None = None  # tope por evento: RAW = p × TAW × Zr
    etc_interval_mm: float | None = None  # ETc de los días del intervalo de reposición (riego localizado)
    dose_limit: DoseLimit | None = None  # qué límite fijó la dosis neta


class SensitivityCase(BaseModel):
    """Qué pasaría si los anclajes se desplazaran un 10 % de su rango."""

    model_config = ConfigDict(frozen=True)

    label: str
    upper_anchor: float
    lower_anchor: float
    irrigation_day: int | None
    net_dose_mm: float | None
    gross_dose_mm: float | None


class Sensitivity(BaseModel):
    model_config = ConfigDict(frozen=True)

    shift_fraction_of_range: float
    shift_index_points: float
    base: SensitivityCase
    cases: tuple[SensitivityCase, ...]
    net_dose_min_mm: float | None  # mínimo y máximo entre el caso base y los desplazados que tienen dosis
    net_dose_max_mm: float | None
    gross_dose_min_mm: float | None
    gross_dose_max_mm: float | None
    cases_without_irrigation: int  # cuántos de los casos (base incluido) no cruzan el umbral en 7 días


class EngineResult(BaseModel):
    """Resultado completo de un cálculo. Se guarda entero en `recommendation` (inmutable)."""

    model_config = ConfigDict(frozen=True)

    method_version: str = METHOD_VERSION
    parameter_set_version: str = PARAMETER_SET_VERSION
    origin_date: dt.date
    period_start: dt.date
    period_end: dt.date
    decision: Decision  # la del primer día del plan (`recommendation.decision`)
    validation_status: ValidationStatus
    indicative: bool = True  # escala relativa del sensor: la dosis es una referencia, no una medida
    indicative_note: str = (
        "Indicativa: la humedad es el índice relativo de 0 a 100 de la sonda, no humedad volumétrica; la dosis es una "
        "referencia calculada con los anclajes de esa escala."
    )
    plan: tuple[DayPlan, ...]
    irrigation_day: int | None = None  # primer día con decisión `irrigate`
    irrigation_date: dt.date | None = None
    net_dose_mm: float | None = None
    gross_dose_mm: float | None = None
    volume_m3: float | None = None
    dose_criterion: DoseCriterion | None = None
    replenishment_interval_days: int | None = None  # solo con criterio de reposición
    dose_on_wetted_strip: bool = False  # True: los mm son sobre la franja mojada (riego localizado)
    factors: tuple[Factor, ...] = ()
    limitations: tuple[Limitation, ...] = ()
    sensitivity: Sensitivity | None = None
