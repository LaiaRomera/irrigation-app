"""Guardar y leer la previsión en `forecast_data` (contrato C3, tarea 8.1).

Una fila por parcela, día previsto (`fecha`), antelación (`lead_days`) y origen (`source`). Para un día de
origen O la previsión son 8 filas: la del día O+k con antelación k, k = 0 a 7. Así, `fecha - lead_days` es
siempre el día de origen, y es lo que usan `load_forecast` y la unicidad de la tabla.

`source` distingue de dónde sale:

- `forecast`: la previsión actual de Open-Meteo (la de hoy, en producción).
- `previous_runs`: las previsiones archivadas (Previous Runs), para repetir un día pasado. Es el nombre que ya
  tenían las filas de ese tipo en la base local.

Decisiones de esta capa (a registrar en `docs/decisiones.md` cuando Laia las apruebe):

- **El día es el día UTC**, como en el entrenamiento y como `target_date` de `ml_prediction`, y no el de la
  zona horaria de la parcela que describe el diccionario de datos. Con la zona de Madrid la diferencia es
  de una o dos horas; usar otro día que el del modelo mezclaría dos definiciones de «día».
- **Repetir un origen no cambia lo ya guardado.** El valor que ya existe se conserva (y su `captured_at`);
  solo se rellenan los huecos (nulos). Así lo que vio el modelo, lo que ve el motor y lo que queda
  archivado son lo mismo, igual que `ml_prediction` es inmutable.
- Solo se guardan las cinco variables que tiene el proyecto: `rainmm`, `eto`, `tmedia`, `rhmedia` y
  `vientomedia`. El resto de columnas se queda en nulo: un valor desconocido no es una cifra supuesta.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import psycopg

from irrigation_app.weather.open_meteo import FORECAST_DAYS

SOURCE_CURRENT = "forecast"
SOURCE_ARCHIVED = "previous_runs"
SOURCES = (SOURCE_CURRENT, SOURCE_ARCHIVED)

# Variable del conector -> columna de `forecast_data`.
COLUMN_MAP = {
    "precipitation_mm": "rainmm",
    "et0_mm": "eto",
    "temperature_c": "tmedia",
    "humidity_pct": "rhmedia",
    "wind_kmh": "vientomedia",
}
WEATHER_COLUMNS = list(COLUMN_MAP.values())
FORECAST_COLUMNS = ["fecha", "lead_days", *WEATHER_COLUMNS, "source"]
MODEL_LEADS = tuple(range(7))  # el modelo usa origen..origen+6
ENGINE_LEADS = tuple(range(1, 8))  # el motor usa origen+1..origen+7
ENGINE_COLUMNS = ("eto", "rainmm")  # del motor solo hacen falta la ET0 y la lluvia


def _origin(origin_day: dt.date | str | pd.Timestamp) -> dt.date:
    return pd.Timestamp(origin_day).date()


def diagonal(daily: pd.DataFrame, origin_day: dt.date | str | pd.Timestamp) -> pd.DataFrame:
    """Las filas de `daily` (día, antelación, variables) que son «el día origen+k con antelación k»."""
    origin = pd.Timestamp(_origin(origin_day), tz="UTC")
    days = pd.to_datetime(daily["day"], utc=True).dt.floor("D")
    offset = ((days - origin) / pd.Timedelta(days=1)).round()
    keep = (offset == daily["lead_days"]) & (daily["lead_days"] >= 0) & (daily["lead_days"] < FORECAST_DAYS)
    return daily[keep.to_numpy()].reset_index(drop=True)


def _value(number) -> float | None:
    """Un número o None: un NaN de pandas nunca se guarda como número."""
    return None if number is None or pd.isna(number) else float(number)


def store_forecast(
    connection: psycopg.Connection,
    plot_id: int,
    daily: pd.DataFrame,
    origin_day: dt.date | str | pd.Timestamp,
    source: str,
    captured_at: dt.datetime | None = None,
) -> int:
    """Guarda la previsión de un origen en `forecast_data`. Devuelve cuántas filas se crearon o se completaron.

    `daily` trae `day`, `lead_days` y las variables del conector (`fetch_forecast` o `fetch_previous_runs`);
    solo entran las filas del día origen+k con antelación k. Lo que ya está guardado no se cambia: si la fila
    existe, solo se rellenan sus huecos. Todo en una transacción.
    """
    if source not in SOURCES:
        raise ValueError(f"source debe ser uno de {', '.join(SOURCES)}")
    rows = diagonal(daily, origin_day)
    captured_at = captured_at or dt.datetime.now(dt.timezone.utc)
    fill = " OR ".join(f"(forecast_data.{c} IS NULL AND EXCLUDED.{c} IS NOT NULL)" for c in WEATHER_COLUMNS)
    sets = ", ".join(f"{c} = COALESCE(forecast_data.{c}, EXCLUDED.{c})" for c in WEATHER_COLUMNS)
    sql = (
        f"INSERT INTO forecast_data (plot_id, fecha, captured_at, lead_days, source, {', '.join(WEATHER_COLUMNS)}) "
        f"VALUES (%s, %s, %s, %s, %s, {', '.join(['%s'] * len(WEATHER_COLUMNS))}) "
        f"ON CONFLICT (plot_id, fecha, lead_days, source) DO UPDATE SET {sets} WHERE {fill}"
    )
    changed = 0
    with connection.transaction(), connection.cursor() as cursor:
        for row in rows.itertuples():
            values = [_value(getattr(row, name)) for name in COLUMN_MAP]
            cursor.execute(
                sql,
                (plot_id, pd.Timestamp(row.day).date(), captured_at, int(row.lead_days), source, *values),
            )
            changed += cursor.rowcount
    return changed


def load_forecast(
    connection: psycopg.Connection,
    plot_id: int,
    origin_day: dt.date | str | pd.Timestamp,
    source: str | None = None,
) -> pd.DataFrame:
    """La previsión guardada de un origen (contrato C3), una fila por día: `FORECAST_COLUMNS`.

    Sin `source`, si hay de las dos procedencias gana la previsión actual. Los números vuelven como
    `float` y un hueco como NaN. Devuelve un marco vacío si no hay nada.
    """
    rows = connection.execute(
        f"SELECT fecha, lead_days, {', '.join(WEATHER_COLUMNS)}, source FROM forecast_data "
        "WHERE plot_id = %s AND fecha - lead_days = %s AND (%s::text IS NULL OR source = %s) "
        "ORDER BY fecha",
        (plot_id, _origin(origin_day), source, source),
    ).fetchall()
    frame = pd.DataFrame(rows, columns=FORECAST_COLUMNS)
    for column in WEATHER_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    if source is None and frame["source"].nunique() > 1:
        chosen = SOURCE_CURRENT if (frame["source"] == SOURCE_CURRENT).any() else frame["source"].iloc[0]
        frame = frame[frame["source"] == chosen].reset_index(drop=True)
    return frame


def missing_weather(
    forecast: pd.DataFrame, leads: tuple[int, ...], columns: tuple[str, ...] = tuple(WEATHER_COLUMNS)
) -> dict[int, list[str]]:
    """Qué falta de la previsión: `{antelación: [columnas sin dato]}`, solo para las antelaciones con algún hueco.

    Una antelación sin fila cuenta como todas las columnas ausentes. `forecast` es lo que devuelve `load_forecast`.
    """
    by_lead = forecast.drop_duplicates("lead_days").set_index("lead_days") if len(forecast) else forecast
    gaps: dict[int, list[str]] = {}
    for lead in leads:
        if len(by_lead) == 0 or lead not in by_lead.index:
            gaps[lead] = list(columns)
            continue
        absent = [c for c in columns if pd.isna(by_lead.loc[lead, c])]
        if absent:
            gaps[lead] = absent
    return gaps
