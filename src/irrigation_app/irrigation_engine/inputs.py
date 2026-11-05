"""Entrada del motor de riego (contrato C2): lo que recibe `calculation.run_engine`.

Todo está validado con Pydantic y un valor imposible se rechaza con un error que nombra el campo. Un valor
desconocido es `None`, nunca una cifra supuesta. No hay red ni base de datos: quien llama (el servicio de
informes) monta esta entrada con lo que leyó de `ml_prediction` (C1), `forecast_data` (C3) y `REFERENCE_PLOT` (C5).

Convención de días: el día `k` del plan (1 a 7) es la fecha `origin_date + k` y usa la predicción de humedad del
horizonte `k` (la humedad de las 05:00 UTC de esa fecha). La meteorología del día `k` es la de esa misma fecha.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from irrigation_app.reference_plot import ReferencePlot

PLAN_DAYS = 7


class PredictionLimitation(BaseModel):
    """Una limitación de una predicción de humedad: un código estable y sus parámetros (por ejemplo `age_hours`)."""

    model_config = ConfigDict(frozen=True, extra="allow")

    code: str = Field(min_length=1)


class SensorPrediction(BaseModel):
    """Una fila de `ml_prediction` (contrato C1): una sonda, un horizonte."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    depth_cm: int = Field(gt=0)
    horizon_days: int = Field(ge=1, le=PLAN_DAYS)
    moisture_pred: float | None = Field(default=None, ge=0, le=100)  # índice 0 a 100, ya sumado a la lectura base
    base_moisture: float | None = Field(default=None, ge=0, le=100)
    method: Literal["model", "persistence_fallback"] | None = None
    quality: Literal["validated", "provisional", "insufficient_data"]
    limitations: tuple[PredictionLimitation, ...] = ()

    @model_validator(mode="after")
    def _usable_prediction_has_a_value(self) -> "SensorPrediction":
        if self.quality != "insufficient_data" and self.moisture_pred is None:
            raise ValueError("moisture_pred no puede ser nulo si quality no es insufficient_data")
        return self


class WeatherDay(BaseModel):
    """Meteorología prevista de una fecha (contrato C3). Un dato ausente es `None`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: dt.date
    eto_mm: float | None = Field(default=None, ge=0, le=25)  # ET₀ de Open-Meteo (no se calcula aquí)
    rain_mm: float | None = Field(default=None, ge=0, le=1000)
    lead_days: int | None = Field(default=None, ge=0, le=16)  # antelación de la previsión


class EngineInput(BaseModel):
    """Todo lo que el motor necesita. Mismas entradas, mismo resultado."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plot: ReferencePlot
    origin_date: dt.date  # día del origen de las predicciones (05:00 UTC)
    predictions: tuple[SensorPrediction, ...] = ()
    weather: tuple[WeatherDay, ...] = ()
    # Eficiencia medida o declarada para este cálculo; si es None se usa la de la tabla según el tipo de riego.
    irrigation_efficiency: float | None = Field(default=None, gt=0, le=1)
    # Intervalo de reposición del riego localizado (días de ETc que repone cada evento). None = 3 días (supuesto).
    replenishment_interval_days: int | None = Field(default=None, ge=1, le=PLAN_DAYS)

    @model_validator(mode="after")
    def _no_duplicates(self) -> "EngineInput":
        keys = [(p.depth_cm, p.horizon_days) for p in self.predictions]
        if len(keys) != len(set(keys)):
            raise ValueError("predictions: hay dos predicciones para la misma sonda y el mismo horizonte")
        dates = [w.date for w in self.weather]
        if len(dates) != len(set(dates)):
            raise ValueError("weather: hay dos filas para la misma fecha")
        return self
