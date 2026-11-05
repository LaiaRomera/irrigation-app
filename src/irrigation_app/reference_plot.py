"""Parcela de referencia (contrato C5): la configuración escrita como código Python.

Es la unidad 1168 (`1168_P2`), confirmada por Laia el 5 de noviembre de 2026: manzano, riego por
goteo de una línea, suelo franco arcilloso limoso. Pydantic la valida al importar. No contiene datos
de la empresa más allá del identificador de la unidad.

Cada dato dice de dónde sale (`source`): `laia` (dato de Laia), `agrodevices` (configuración de la
sonda) o `assumption` (supuesto declarado). Un valor desconocido es `None`, nunca una cifra supuesta.
Los anclajes se copian aquí solo tras revisarlos con Laia; hasta entonces llevan `review="pending"`,
y al confirmarse anotan quién y cuándo (`reviewed_by`, `reviewed_on`).
"""

from __future__ import annotations

import datetime as dt
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, model_validator

T = TypeVar("T")
Source = Literal["laia", "agrodevices", "assumption"]


class Sourced(BaseModel, Generic[T]):
    """Un dato con su origen y, si hace falta, una nota."""

    model_config = ConfigDict(frozen=True)

    value: T
    source: Source
    note: str = ""


class Anchor(BaseModel):
    """Un anclaje de la escala del sensor: índice de 0 a 100 de la media de las capas de la zona radicular."""

    model_config = ConfigDict(frozen=True)

    index: float
    frequency: float | None = None  # frecuencia original de la sonda, si el anclaje sale de Agrodevices
    source: Literal["agrodevices", "data", "generic"]
    method: str
    period: str | None = None  # solo si salió de los datos
    review: Literal["pending", "confirmed"] = "pending"
    reviewed_by: str | None = None  # quién lo revisó (obligatorio si review == "confirmed")
    reviewed_on: dt.date | None = None  # cuándo

    @model_validator(mode="after")
    def _confirmed_needs_reviewer(self) -> "Anchor":
        if self.review == "confirmed" and (self.reviewed_by is None or self.reviewed_on is None):
            raise ValueError("un anclaje confirmado necesita reviewed_by y reviewed_on")
        return self


class Anchors(BaseModel):
    """Anclajes globales (no uno por profundidad) para la media de `ReferencePlot.average_depths_cm`."""

    model_config = ConfigDict(frozen=True)

    upper: Anchor  # suelo lleno (saturación)
    lower: Anchor  # punto de marchitez permanente
    # Otras líneas de Agrodevices, solo informativas: nombre -> índice
    other_lines: dict[str, float] = {}

    @model_validator(mode="after")
    def _upper_above_lower(self) -> "Anchors":
        if not self.upper.index > self.lower.index:
            raise ValueError("el anclaje superior debe ser mayor que el inferior")
        return self


class InstallationDays(BaseModel):
    """Los días excluidos por instalación de la sonda: se citan, no se copian."""

    model_config = ConfigDict(frozen=True)

    rule: str  # dónde está la regla
    summary: str  # resultado para esta unidad


class ReferencePlot(BaseModel):
    """Parcela de referencia. Obligatorios para confirmarla: cultivo y textura del suelo."""

    model_config = ConfigDict(frozen=True)

    unit_id: int
    name: str
    status: Literal["candidate", "confirmed"]
    confirmed_on: dt.date | None = None
    crop: Sourced[str] | None = None
    soil_texture: Sourced[str] | None = None
    irrigation_type: Sourced[str] | None = None  # opcional
    cycle_start_date: Sourced[dt.date] | None = None  # opcional
    area_m2: Sourced[float] | None = None  # opcional
    # Cubierta vegetal entre las filas: `active_cover` o `no_cover`. Opcional; el motor elige el caso de Kc
    # (Tabla 12) con ella. Si su origen es `assumption`, el resultado lleva la limitación `assumed_ground_cover`.
    ground_cover: Sourced[Literal["active_cover", "no_cover"]] | None = None
    average_depths_cm: tuple[int, ...]
    sensor_depths_cm: tuple[int, ...]
    smm_min: int
    smm_max: int
    installation: InstallationDays
    anchors: Anchors | None = None
    localized_irrigation_note: str = ""

    @model_validator(mode="after")
    def _confirmed_needs_crop_and_texture(self) -> "ReferencePlot":
        if self.status == "confirmed":
            missing = [n for n, v in (("crop", self.crop), ("soil_texture", self.soil_texture)) if v is None]
            if missing:
                raise ValueError(f"una parcela confirmada necesita {' y '.join(missing)}")
        return self

    @model_validator(mode="after")
    def _average_depths_are_sensor_depths(self) -> "ReferencePlot":
        if not set(self.average_depths_cm) <= set(self.sensor_depths_cm):
            raise ValueError("average_depths_cm debe estar dentro de sensor_depths_cm")
        return self


REFERENCE_PLOT = ReferencePlot(
    unit_id=1168,
    name="1168_P2",
    status="confirmed",
    confirmed_on=dt.date(2026, 11, 5),
    crop=Sourced(value="manzano", source="laia", note="Confirmado por Laia el 5 de noviembre de 2026."),
    soil_texture=Sourced(
        value="franco arcilloso limoso", source="laia", note="Dato de Laia, 5 de noviembre de 2026; sin análisis de suelo."
    ),
    irrigation_type=Sourced(value="drip_one_line", source="laia", note="Goteo de una línea (Laia, 5 de noviembre de 2026)."),
    cycle_start_date=None,  # desconocida: el motor usa una etapa estimada (assumed_crop_stage)
    area_m2=None,  # desconocida: sin superficie no hay volumen, solo mm
    ground_cover=Sourced(
        value="active_cover",
        source="assumption",
        note="Supuesto de Laia, sin confirmar (5 de noviembre de 2026): cree que hay vegetación entre las filas. "
        "Se preguntará a Agrodevices. Sin cubierta, el Kc de pleno verano baja de 1,20 a 0,95 (Tabla 12) y la dosis con él.",
    ),
    average_depths_cm=(15, 25, 35),  # las marcadas como incluidas en la media de la unidad; la de 55 cm no entra
    sensor_depths_cm=(15, 25, 35, 55),
    smm_min=6900,
    smm_max=11700,
    installation=InstallationDays(
        rule="irrigation_marks.installation_days(snapshots, admin=admin_days(daily)): unión de la regla automática, "
        "la lista manual y los días con lecturas SMMAdmin",
        summary="En la 1168, del 9 al 15 de marzo de 2025 (7 días), cifra del 5 de noviembre de 2026.",
    ),
    # Fuente preferente: líneas de Agrodevices, calibradas a mano según Laia (5 de noviembre de 2026),
    # convertidas con (smm_max - f) / (smm_max - smm_min) * 100. Confirmadas por Laia el 5 de noviembre de 2026.
    anchors=Anchors(
        upper=Anchor(
            index=(11700 - 7889) / (11700 - 6900) * 100,
            frequency=7889,
            source="agrodevices",
            method="Línea de saturación de Agrodevices (100 %), convertida al índice con el rango de la unidad",
            review="confirmed",
            reviewed_by="Laia",
            reviewed_on=dt.date(2026, 11, 5),
        ),
        lower=Anchor(
            index=(11700 - 8834) / (11700 - 6900) * 100,
            frequency=8834,
            source="agrodevices",
            method="Línea del punto de marchitez permanente de Agrodevices (0 %), convertida al índice con el rango de la unidad",
            review="confirmed",
            reviewed_by="Laia",
            reviewed_on=dt.date(2026, 11, 5),
        ),
        other_lines={
            "upper_optimal_recharge": (11700 - 8234) / (11700 - 6900) * 100,
            "upper_hard_to_use_reserve": (11700 - 8570) / (11700 - 6900) * 100,
        },
    ),
    localized_irrigation_note=(
        "El goteo de una línea moja solo una franja del suelo: se aplica el criterio FAO-56 de riego localizado "
        "(fracción mojada fw, Tabla 20, y few = fw·[1 − (2/3)fc], capítulo 7). La sonda mide un punto de esa "
        "franja y la dosis es una referencia indicativa."
    ),
)
