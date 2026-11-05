"""Variables meteorológicas de cada ejemplo de la tabla de entrenamiento (tarea 1.3, decisión D1).

Un ejemplo de horizonte N predice el cambio de humedad entre las 05:00 UTC del día de origen y las
de N días después, así que le importa el tiempo de los días origen+0 a origen+N−1. Cada día k se
toma con su antelación k (`lead_days = k`): es la previsión que habrá en producción, donde solo
existe la de hoy, menos exacta cuanto más lejos. El de horizonte 1 solo ve el día de origen con
antelación 0; el de horizonte 3 ve tres días con antelaciones 0, 1 y 2.

Variables resultantes: suma de lluvia y de ET0 de esos días, y media de temperatura, humedad y
viento. Si falta cualquiera de los datos de cualquiera de los días, esa variable queda **nula**
(no se rellena) y el ejemplo se marca con `weather_incomplete = True`.

Estas funciones sirven igual para entrenar y para predecir: en la inferencia se les pasa la
previsión de hoy (cada día con su antelación) en el mismo formato que `previous_runs.parquet`.
"""

from __future__ import annotations

import pandas as pd

KEY_COLUMNS = ["unit_id", "origin_day", "horizon_days"]
WEATHER_INPUT_COLUMNS = ["precipitation_mm", "et0_mm", "temperature_c", "humidity_pct", "wind_kmh"]
# Entrada -> (nombre de la variable de salida, cómo se resume).
FEATURE_SUMMARY = {
    "precipitation_mm": ("precip_sum_mm", "sum"),
    "et0_mm": ("et0_sum_mm", "sum"),
    "temperature_c": ("temp_mean_c", "mean"),
    "humidity_pct": ("humidity_mean_pct", "mean"),
    "wind_kmh": ("wind_mean_kmh", "mean"),
}
WEATHER_FEATURE_COLUMNS = [name for name, _ in FEATURE_SUMMARY.values()]
INCOMPLETE_COLUMN = "weather_incomplete"
WEATHER_KEY = ["location_id", "day", "lead_days"]


def _utc_day(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True).dt.floor("D")


def forecast_days(examples: pd.DataFrame) -> pd.DataFrame:
    """Los días de meteorología de cada ejemplo: una fila por ejemplo y día, con su antelación.

    Para el horizonte N devuelve N filas (`lead_days` = 0 a N-1, `day` = origen + `lead_days`).
    Columnas: `unit_id`, `origin_day`, `horizon_days`, `day` y `lead_days`.
    """
    keys = examples[KEY_COLUMNS].drop_duplicates().reset_index(drop=True)
    keys["origin_day"] = _utc_day(keys["origin_day"])
    keys["horizon_days"] = keys["horizon_days"].astype(int)
    days = keys.loc[keys.index.repeat(keys["horizon_days"])].reset_index(drop=True)
    days["lead_days"] = days.groupby(KEY_COLUMNS, sort=False).cumcount()
    days["day"] = days["origin_day"] + pd.to_timedelta(days["lead_days"], unit="D")
    return days


def weather_features(
    examples: pd.DataFrame,
    weather: pd.DataFrame,
    unit_locations: pd.DataFrame,
) -> pd.DataFrame:
    """Una fila por (unidad, origen, horizonte) con las variables meteorológicas y `weather_incomplete`.

    `examples` necesita `unit_id`, `origin_day` y `horizon_days` (puede repetir filas, por ejemplo una
    por profundidad). `weather` tiene `location_id`, `day`, `lead_days` y las cinco variables diarias
    de Open-Meteo. `unit_locations` relaciona `unit_id` con `location_id`. Una unidad sin ubicación,
    o un día/antelación sin dato, deja las variables en nulo y el ejemplo incompleto.
    """
    days = forecast_days(examples)
    mapping = unit_locations[["unit_id", "location_id"]].drop_duplicates("unit_id")
    days = days.merge(mapping, on="unit_id", how="left")

    daily = weather[[*WEATHER_KEY, *WEATHER_INPUT_COLUMNS]].copy()
    daily["day"] = _utc_day(daily["day"])
    if daily.duplicated(WEATHER_KEY).any():
        raise ValueError("La meteorología tiene filas repetidas para la misma ubicación, día y antelación")
    days = days.merge(daily, on=WEATHER_KEY, how="left")

    grouped = days.groupby(KEY_COLUMNS, sort=False)
    horizon = grouped["horizon_days"].first()
    result = pd.DataFrame(index=horizon.index)
    for source, (name, how) in FEATURE_SUMMARY.items():
        # Basta un día sin dato para que el total quede nulo: no se rellena ni se promedia lo que hay.
        total = grouped[source].sum(min_count=1).where(grouped[source].count() == horizon)
        result[name] = total / horizon if how == "mean" else total
    result[INCOMPLETE_COLUMN] = result[WEATHER_FEATURE_COLUMNS].isna().any(axis=1)
    return result.reset_index()


def add_weather_features(
    examples: pd.DataFrame,
    weather: pd.DataFrame,
    unit_locations: pd.DataFrame,
) -> pd.DataFrame:
    """Devuelve `examples` (mismas filas y orden) con las columnas meteorológicas añadidas."""
    features = weather_features(examples, weather, unit_locations)
    features["_origin_utc"] = features.pop("origin_day")
    keyed = examples.assign(_origin_utc=_utc_day(examples["origin_day"]))
    merged = keyed.merge(
        features, on=["unit_id", "_origin_utc", "horizon_days"], how="left", validate="many_to_one"
    )
    merged.index = examples.index
    return merged.drop(columns="_origin_utc")
