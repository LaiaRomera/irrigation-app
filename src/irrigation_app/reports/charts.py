"""Gráficas del informe con Matplotlib (tarea 7.3). Devuelven PNG en bytes; el informe los incrusta en el HTML.

Se usa la API de `Figure` sin `pyplot`, para no depender de un estado global ni de una ventana. Todas las
cifras salen del `EngineResult` (y, para la humedad por profundidad, de la `EngineInput` que se le dio al motor).
"""

from __future__ import annotations

import base64
import datetime as dt
import io
from dataclasses import dataclass, field

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Patch

from irrigation_app.irrigation_engine.inputs import EngineInput
from irrigation_app.irrigation_engine.result import EngineResult
from irrigation_app.reports.metrics import HorizonError

BLUE, ORANGE, GREEN, GREY, RED = "#1f5f99", "#c2570c", "#2e7d32", "#8a8f98", "#b3261e"
_DECISION_COLOR = {"irrigate": GREEN, "postpone": ORANGE, "do_not_irrigate": GREY, "insufficient_data": RED}


@dataclass(frozen=True)
class ChartData:
    """Las series de los 7 días del plan. Un valor desconocido es `None` y deja un hueco en la gráfica."""

    dates: list[dt.date]
    days: list[int]
    mean: list[float | None]
    threshold: list[float | None]  # índice de humedad en el que se cruza el umbral de riego (depende de p cada día)
    upper_anchor: float | None
    lower_anchor: float | None
    rain_mm: list[float | None]
    net_dose_mm: list[float | None]
    decisions: list[str]
    per_depth: dict[int, list[float | None]] = field(default_factory=dict)
    irrigation_day: int | None = None


def _factor_value(result: EngineResult, name: str) -> float | None:
    for f in result.factors:
        if f.name == name and isinstance(f.value, (int, float)):
            return float(f.value)
    return None


def chart_data(result: EngineResult, engine_input: EngineInput | None = None) -> ChartData:
    """Series para las gráficas. El umbral del día es `superior − p × (superior − inferior)`, como en el motor."""
    upper, lower = _factor_value(result, "anchor_upper"), _factor_value(result, "anchor_lower")
    threshold = [
        None if upper is None or lower is None or d.p is None else upper - d.p * (upper - lower) for d in result.plan
    ]
    per_depth: dict[int, list[float | None]] = {}
    if engine_input is not None:
        by_key = {(p.depth_cm, p.horizon_days): p.moisture_pred for p in engine_input.predictions}
        for depth in sorted({p.depth_cm for p in engine_input.predictions}):
            per_depth[depth] = [by_key.get((depth, d.day)) for d in result.plan]
    return ChartData(
        dates=[d.date for d in result.plan], days=[d.day for d in result.plan],
        mean=[d.moisture_mean for d in result.plan], threshold=threshold, upper_anchor=upper, lower_anchor=lower,
        rain_mm=[d.rain_mm for d in result.plan], net_dose_mm=[d.net_dose_mm for d in result.plan],
        decisions=[d.decision for d in result.plan], per_depth=per_depth, irrigation_day=result.irrigation_day,
    )


def _png(fig: Figure) -> bytes:
    FigureCanvasAgg(fig)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=150, bbox_inches="tight")
    return buffer.getvalue()


def _gaps(values: list[float | None]) -> list[float]:
    return [float("nan") if v is None else v for v in values]


def _day_labels(data: ChartData) -> list[str]:
    return [f"Día {d}\n{date:%d/%m}" for d, date in zip(data.days, data.dates)]


def moisture_chart(data: ChartData) -> bytes:
    """Humedad prevista (media de la zona radicular y cada profundidad) frente al umbral de riego."""
    fig = Figure(figsize=(7.2, 3.4))
    ax = fig.add_subplot(111)
    x = list(range(len(data.days)))
    for depth, series in data.per_depth.items():
        ax.plot(x, _gaps(series), color=GREY, linewidth=1, linestyle="--", marker=".", label=f"{depth} cm")
    ax.plot(x, _gaps(data.mean), color=BLUE, linewidth=2.4, marker="o", label="Media de la zona radicular")
    ax.plot(x, _gaps(data.threshold), color=ORANGE, linewidth=1.8, label="Umbral de riego")
    if data.lower_anchor is not None:
        ax.axhline(data.lower_anchor, color=RED, linewidth=0.8, linestyle=":", label="Punto de marchitez (anclaje)")
    if data.upper_anchor is not None:
        ax.axhline(data.upper_anchor, color=GREEN, linewidth=0.8, linestyle=":", label="Suelo lleno (anclaje)")
    if data.irrigation_day is not None:
        ax.axvline(data.irrigation_day - 1, color=GREEN, alpha=0.25, linewidth=10)
    ax.set_xticks(x, _day_labels(data), fontsize=8)
    ax.set_ylabel("Índice de humedad (0 a 100)", fontsize=9)
    ax.set_title("Humedad prevista del suelo y umbral de riego", fontsize=10)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=7, loc="best", ncol=2)
    fig.text(0.5, -0.02, "Índice relativo de la sonda: recomendación indicativa. La banda verde es el día de riego "
             "orientativo, no un día exacto.", ha="center", fontsize=7, color=GREY)
    return _png(fig)


def plan_chart(data: ChartData) -> bytes:
    """Plan de 7 días: dosis neta de riego (mm) y lluvia prevista (mm) por día."""
    fig = Figure(figsize=(7.2, 3.0))
    ax = fig.add_subplot(111)
    x = list(range(len(data.days)))
    width = 0.38
    rain = [0.0 if v is None else v for v in data.rain_mm]
    dose = [0.0 if v is None else v for v in data.net_dose_mm]
    ax.bar([i - width / 2 for i in x], rain, width, color="#7fb3d5", label="Lluvia prevista (mm)")
    bars = ax.bar([i + width / 2 for i in x], dose, width, color=[_DECISION_COLOR[d] for d in data.decisions])
    for bar, value, decision in zip(bars, data.net_dose_mm, data.decisions):
        if value is not None:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{value:.1f}".replace(".", ","),
                    ha="center", va="bottom", fontsize=7)
        elif decision == "postpone":
            ax.text(bar.get_x() + bar.get_width() / 2, 0, "aplazar", ha="center", va="bottom", fontsize=7, color=ORANGE)
    ax.set_xticks(x, _day_labels(data), fontsize=8)
    ax.set_ylabel("mm", fontsize=9)
    ax.set_title("Plan de 7 días: dosis (sobre la franja mojada) y lluvia", fontsize=10)
    ax.grid(axis="y", alpha=0.25)
    top = max([*rain, *dose, 1.0])
    ax.set_ylim(0, top * 1.35)  # hueco arriba para la leyenda y las etiquetas
    ax.legend(handles=[Patch(color="#7fb3d5", label="Lluvia prevista (mm)"),
                       Patch(color=GREEN, label="Dosis neta de riego (mm)")], fontsize=7, loc="upper right", ncol=2)
    return _png(fig)


def error_chart(errors: tuple[HorizonError, ...]) -> bytes:
    """Error típico (MAE) de la predicción de humedad por horizonte, en puntos del índice."""
    fig = Figure(figsize=(7.2, 2.6))
    ax = fig.add_subplot(111)
    horizons = [e.horizon_days for e in errors]
    maes = [e.mae for e in errors]
    bars = ax.bar(horizons, maes, color=BLUE, width=0.6)
    for bar, value in zip(bars, maes):
        ax.text(bar.get_x() + bar.get_width() / 2, value, f"{value:.2f}".replace(".", ","), ha="center",
                va="bottom", fontsize=8)
    ax.set_xticks(horizons, [f"{h} día" + ("s" if h > 1 else "") for h in horizons], fontsize=8)
    ax.set_ylabel("Error típico (puntos del índice)", fontsize=9)
    ax.set_title("Error típico de la predicción de humedad por horizonte", fontsize=10)
    ax.grid(axis="y", alpha=0.25)
    return _png(fig)


def to_data_uri(png: bytes) -> str:
    """El PNG como `data:` URI, para que el HTML (y el PDF) no dependan de archivos sueltos."""
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
