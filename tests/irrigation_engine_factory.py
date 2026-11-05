"""Ayudas de las pruebas del motor: entradas sintéticas que cumplen los contratos C1, C3 y C5 (sin datos reales)."""

import datetime as dt

from irrigation_app.irrigation_engine import EngineInput, SensorPrediction, WeatherDay
from irrigation_app.reference_plot import REFERENCE_PLOT, ReferencePlot, Sourced

ORIGIN = dt.date(2026, 8, 25)  # un día de campaña


def plot_with(**overrides) -> ReferencePlot:
    """La parcela de referencia con campos cambiados (se vuelve a validar)."""
    data = REFERENCE_PLOT.model_dump()
    data.update(overrides)
    return ReferencePlot.model_validate(data)


def complete_plot(**overrides) -> ReferencePlot:
    """Parcela sin supuestos: cubierta y fecha de inicio dadas por Laia."""
    base = dict(
        ground_cover=Sourced(value="active_cover", source="laia"),
        cycle_start_date=Sourced(value=dt.date(2026, 3, 15), source="laia"),
    )
    base.update(overrides)
    return plot_with(**base)


def predictions(levels: dict[int, float], depths=(15, 25, 35), **kw) -> list[SensorPrediction]:
    """Una predicción por sonda y día; `levels` da la humedad media de cada horizonte (1 a 7)."""
    out = []
    for k, level in levels.items():
        for depth in depths:
            out.append(SensorPrediction(depth_cm=depth, horizon_days=k, moisture_pred=level, base_moisture=level,
                                        method="model", quality="validated", **kw))
    return out


def weather(origin=ORIGIN, eto=5.0, rain=0.0, days=range(0, 8), rain_by_day=None) -> list[WeatherDay]:
    rain_by_day = rain_by_day or {}
    return [WeatherDay(date=origin + dt.timedelta(days=k), eto_mm=eto, rain_mm=rain_by_day.get(k, rain), lead_days=k)
            for k in days]


def engine_input(levels, plot=None, origin=ORIGIN, preds=None, wx=None, **kw) -> EngineInput:
    return EngineInput(
        plot=plot or REFERENCE_PLOT, origin_date=origin,
        predictions=tuple(preds if preds is not None else predictions(levels)),
        weather=tuple(wx if wx is not None else weather(origin)), **kw,
    )


# Humedad media (índice) por día: baja despacio y cruza p el día 4 (anclajes 79,40 y 59,71; p ≈ 0,46 con ETc 6 mm)
CROSS_ON_DAY_4 = {1: 73.0, 2: 72.0, 3: 71.0, 4: 69.0, 5: 67.0, 6: 65.0, 7: 63.0}
WET = {k: 76.0 for k in range(1, 8)}
