"""Lo que el motor de riego lee de la base de datos para un origen (contratos C1 y C3 hacia C2).

`load_engine_feed` devuelve, para una parcela y un día de origen, las predicciones de humedad guardadas en
`ml_prediction` y la previsión de `forecast_data` de origen+1 a origen+7, con los nombres de campo de
`irrigation_engine.inputs` (`SensorPrediction` y `WeatherDay`), sin importar el motor: quien monta la
entrada (el servicio de informes) hace `SensorPrediction(**fila)` y `WeatherDay(**fila)`. Un día de
previsión que no está guardado no aparece (el motor lo trata como ausente); un valor desconocido es `None`.

Solo lee: no calcula ni escribe nada.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import psycopg

from irrigation_app.models.inference import origin_timestamp
from irrigation_app.weather.forecast_store import ENGINE_LEADS, load_forecast


def _number(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def load_engine_feed(
    connection: psycopg.Connection,
    plot_id: int,
    origin_day: dt.date | str | pd.Timestamp,
    forecast_source: str | None = None,
) -> dict[str, Any]:
    """`{"origin_date", "predictions", "weather"}` para el motor; las dos listas pueden venir vacías."""
    origin_at = origin_timestamp(origin_day)
    rows = connection.execute(
        "SELECT s.depth_cm, p.horizon_days, p.moisture_pred, p.base_moisture, p.method, p.quality, p.limitations "
        "FROM ml_prediction p JOIN sensor s USING (sensor_id) "
        "WHERE s.plot_id = %s AND p.origin_at = %s ORDER BY s.depth_cm, p.horizon_days",
        (plot_id, origin_at.to_pydatetime()),
    ).fetchall()
    predictions = [
        {
            "depth_cm": int(depth), "horizon_days": int(horizon), "moisture_pred": _number(pred),
            "base_moisture": _number(base), "method": method, "quality": quality, "limitations": list(limitations or []),
        }
        for depth, horizon, pred, base, method, quality, limitations in rows
    ]

    forecast = load_forecast(connection, plot_id, origin_at.date(), forecast_source)
    forecast = forecast[forecast["lead_days"].isin(ENGINE_LEADS)]
    weather = [
        {"date": r.fecha, "eto_mm": _number(r.eto), "rain_mm": _number(r.rainmm), "lead_days": int(r.lead_days)}
        for r in forecast.itertuples()
    ]
    return {"origin_date": origin_at.date(), "predictions": predictions, "weather": weather}
