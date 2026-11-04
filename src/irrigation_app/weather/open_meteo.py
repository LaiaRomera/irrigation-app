"""Conector de Open-Meteo: previsiones archivadas (Previous Runs) resumidas a valores diarios.

Previous Runs solo ofrece valores por horas: lo que el modelo había previsto `N` días antes de
cada hora. Aquí se piden las horas y se resumen por día UTC (el mismo día que usan las lecturas
de los sensores): suma para lluvia y ET₀, media para el resto.
"""

from __future__ import annotations

import time
from datetime import date

import pandas as pd
import requests

PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_SECONDS = 120
ATTEMPTS = 3
RETRY_WAIT_SECONDS = 5
HOURS_PER_DAY = 24

# Nombre propio -> (variable de Open-Meteo, cómo se resume un día).
DAILY_VARIABLES = {
    "precipitation_mm": ("precipitation", "sum"),
    "et0_mm": ("et0_fao_evapotranspiration", "sum"),
    "temperature_c": ("temperature_2m", "mean"),
    "humidity_pct": ("relative_humidity_2m", "mean"),
    "wind_kmh": ("wind_speed_10m", "mean"),
}
DAILY_COLUMNS = ["day", "lead_days", *DAILY_VARIABLES]


class WeatherError(RuntimeError):
    """Open-Meteo no respondió o respondió algo inesperado."""


class RateLimitedError(WeatherError):
    """Open-Meteo respondió 429: se superó un límite de la capa gratuita (el motivo viene en el mensaje)."""


def _column(variable: str, lead_days: int) -> str:
    return f"{variable}_previous_day{lead_days}"


def _get_json(url: str, params: dict, session) -> dict:
    """Hace la petición y devuelve el JSON. Los errores no incluyen la URL (lleva coordenadas)."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = (session or requests).get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            break
        except requests.exceptions.RequestException as error:
            # El servidor a veces responde muy lento un momento: se reintenta antes de rendirse.
            if attempt == ATTEMPTS:
                raise WeatherError(f"Open-Meteo no respondió ({type(error).__name__})") from error
            time.sleep(RETRY_WAIT_SECONDS * attempt)
    if response.status_code >= 400:
        try:
            reason = str(response.json().get("reason", ""))
        except (ValueError, AttributeError):
            reason = ""
        error_class = RateLimitedError if response.status_code == 429 else WeatherError
        raise error_class(f"Open-Meteo respondió {response.status_code}" + (f": {reason}" if reason else ""))
    return response.json()


def previous_runs_to_daily(data: dict, lead_days: tuple[int, ...]) -> pd.DataFrame:
    """Resume a días UTC la respuesta por horas de Previous Runs.

    Un día solo cuenta con sus 24 horas completas: si falta alguna, el valor queda vacío. Los días
    sin ningún valor (por ejemplo, antes de que existan las previsiones archivadas) no se devuelven.
    """
    hourly = data.get("hourly")
    if not hourly or "time" not in hourly:
        raise WeatherError("La respuesta de Open-Meteo no trae datos por horas")

    day = pd.to_datetime(pd.Series(hourly["time"]), utc=True).dt.floor("D")
    frames = []
    for lead in lead_days:
        values = {}
        for name, (variable, how) in DAILY_VARIABLES.items():
            column = _column(variable, lead)
            if column not in hourly:
                raise WeatherError(f"La respuesta de Open-Meteo no trae {column}")
            series = pd.Series(hourly[column], dtype="float64")
            grouped = series.groupby(day)
            values[name] = getattr(grouped, how)().where(grouped.count() == HOURS_PER_DAY)
        frame = pd.DataFrame(values)
        frame.index.name = "day"
        frame = frame.reset_index().assign(lead_days=lead)
        frames.append(frame[frame[list(DAILY_VARIABLES)].notna().any(axis=1)])
    return pd.concat(frames, ignore_index=True)[DAILY_COLUMNS]


def fetch_previous_runs(
    latitude: float,
    longitude: float,
    start: date,
    end: date,
    lead_days: tuple[int, ...] = (1,),
    session=None,
) -> pd.DataFrame:
    """Previsiones diarias de una ubicación entre dos fechas (inclusive), con `lead_days` días de antelación.

    Una fila por día y antelación, con las variables de `DAILY_VARIABLES`. `session` permite
    sustituir `requests` en las pruebas.
    """
    params = {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "hourly": ",".join(_column(variable, lead) for lead in lead_days for variable, _ in DAILY_VARIABLES.values()),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "UTC",
    }
    return previous_runs_to_daily(_get_json(PREVIOUS_RUNS_URL, params, session), lead_days)
