"""Conector de Open-Meteo: previsiones archivadas (Previous Runs) resumidas a valores diarios.

Previous Runs solo ofrece valores por horas: lo que el modelo había previsto `N` días antes de
cada hora. Aquí se piden las horas y se resumen por día UTC (el mismo día que usan las lecturas
de los sensores): suma para lluvia y ET₀, media para el resto.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd
import requests

PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_SECONDS = 120
MAX_DAYS_PER_REQUEST = 60
ATTEMPTS = 3
RETRY_WAIT_SECONDS = 5
TRANSIENT_STATUS_CODES = (502, 503, 504)
HOURS_PER_DAY = 24

# Antelación de la previsión (`lead_days`): `k` es «la previsión que se hizo k días antes del día
# previsto»; la 0 es la ejecución más reciente, la del propio día. Un ejemplo de horizonte N
# predice el cambio entre las 05:00 UTC del día de origen y las 05:00 UTC N días después, así que
# le importa el tiempo de los días de origen a origen+N−1 (el del propio origen incluido, que es
# el que está cayendo), cada día con su antelación: el día origen+k con la antelación k. En
# producción solo existe la previsión de hoy, que para cada día tiene justo esa antelación y es
# menos exacta cuanto más lejos. Si se entrenara con la más exacta, el modelo se fiaría de una
# calidad que no tendrá al usarlo y fallaría. Por eso se piden las antelaciones de 0 a 6.
FORECAST_HORIZON_DAYS = 7
ALL_LEAD_DAYS = tuple(range(FORECAST_HORIZON_DAYS))

# Previsión actual (tarea 8.1): 8 días, antelaciones 0 a 7. El modelo usa origen..origen+6 (horizonte 7)
# y el motor de riego origen+1..origen+7 (contrato C2), así que hacen falta 8.
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 8
MAX_FORECAST_DAYS = 16  # el máximo de `forecast_days` de Open-Meteo

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
    # La antelación 0 es la ejecución más reciente y Open-Meteo la devuelve con el nombre de la
    # variable a secas, sin sufijo (comprobado el 4 de noviembre de 2026).
    return variable if lead_days == 0 else f"{variable}_previous_day{lead_days}"


def _reason(response) -> str:
    """El motivo que Open-Meteo da en el cuerpo de un error, o "" si no hay o no se puede leer."""
    try:
        return str(response.json().get("reason", ""))
    except (ValueError, AttributeError):
        return ""


def _is_transient(response) -> bool:
    """Fallos que suelen pasar solos: error del servidor (502, 503, 504) o demasiadas peticiones a la vez.

    Un 429 por límite de la capa gratuita (por hora o por día) no es transitorio: reintentar no sirve.
    """
    if response.status_code in TRANSIENT_STATUS_CODES:
        return True
    return response.status_code == 429 and "concurrent" in _reason(response).lower()


def _get_json(url: str, params: dict, session) -> dict:
    """Hace la petición y devuelve el JSON. Los errores no incluyen la URL (lleva coordenadas)."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = (session or requests).get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.exceptions.RequestException as error:
            # El servidor a veces responde muy lento un momento: se reintenta antes de rendirse.
            if attempt == ATTEMPTS:
                raise WeatherError(f"Open-Meteo no respondió ({type(error).__name__})") from error
            time.sleep(RETRY_WAIT_SECONDS * attempt)
            continue
        if attempt < ATTEMPTS and _is_transient(response):
            time.sleep(RETRY_WAIT_SECONDS * attempt)
            continue
        break
    if response.status_code >= 400:
        reason = _reason(response)
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


def forecast_to_daily(data: dict, days: int) -> pd.DataFrame:
    """Resume a días UTC la respuesta por horas de la previsión actual.

    La antelación de cada día es su distancia al primer día de la respuesta (el de hoy, antelación 0):
    es la previsión que existe en producción, donde el día origen+k tiene antelación k. Un día solo
    cuenta con sus 24 horas completas (como en `previous_runs_to_daily`); los días sin ningún valor no
    se devuelven, así que una respuesta incompleta da menos filas, nunca valores inventados.
    """
    daily = previous_runs_to_daily(data, (0,))
    first_day = pd.to_datetime(data["hourly"]["time"][0], utc=True).floor("D")
    daily["lead_days"] = ((daily["day"] - first_day) / pd.Timedelta(days=1)).round().astype(int)
    return daily[daily["lead_days"] < days].reset_index(drop=True)[DAILY_COLUMNS]


def fetch_forecast(latitude: float, longitude: float, days: int = FORECAST_DAYS, session=None) -> pd.DataFrame:
    """Previsión actual de una ubicación para `days` días a partir de hoy (día UTC), una fila por día.

    En producción no hay Previous Runs: la previsión de hoy es la antelación 0 para hoy, 1 para mañana,
    etc. (`lead_days` de 0 a `days - 1`). Mismas columnas y unidades que `fetch_previous_runs`. Son 5
    variables por 8 días: una petición que cuenta como una sola llamada de la cuota. `session` permite
    sustituir `requests` en las pruebas.
    """
    if not 1 <= days <= MAX_FORECAST_DAYS:
        raise ValueError(f"days debe estar entre 1 y {MAX_FORECAST_DAYS}")
    params = {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "hourly": ",".join(variable for variable, _ in DAILY_VARIABLES.values()),
        "forecast_days": days,
        "timezone": "UTC",
    }
    return forecast_to_daily(_get_json(FORECAST_URL, params, session), days)


def fetch_previous_runs(
    latitude: float,
    longitude: float,
    start: date,
    end: date,
    lead_days: tuple[int, ...] = ALL_LEAD_DAYS,
    session=None,
) -> pd.DataFrame:
    """Previsiones diarias de una ubicación entre dos fechas (inclusive), con `lead_days` días de antelación.

    Por defecto, todas las antelaciones de 0 a 6 (ver `ALL_LEAD_DAYS`).

    Una fila por día y antelación, con las variables de `DAILY_VARIABLES`. Los rangos largos se
    piden en trozos de `MAX_DAYS_PER_REQUEST` días: con 35 variables una sola petición de casi tres
    años a veces no responde a tiempo, y partida cuesta lo mismo de cuota. `session` permite
    sustituir `requests` en las pruebas.
    """
    hourly = ",".join(_column(variable, lead) for lead in lead_days for variable, _ in DAILY_VARIABLES.values())
    pieces = []
    window_start = start
    while window_start <= end:
        window_end = min(window_start + timedelta(days=MAX_DAYS_PER_REQUEST - 1), end)
        params = {
            "latitude": float(latitude),
            "longitude": float(longitude),
            "hourly": hourly,
            "start_date": window_start.isoformat(),
            "end_date": window_end.isoformat(),
            "timezone": "UTC",
        }
        pieces.append(previous_runs_to_daily(_get_json(PREVIOUS_RUNS_URL, params, session), lead_days))
        window_start = window_end + timedelta(days=1)
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=DAILY_COLUMNS)
