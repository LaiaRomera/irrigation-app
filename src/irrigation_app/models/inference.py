"""Predicción operativa de la humedad (tarea 4.4, contrato C1).

Dada una fecha de origen (las 05:00 UTC de ese día) y las lecturas de las sondas de una parcela, predice
el nivel de humedad de cada sonda a 1 a 7 días: el modelo de cada horizonte predice el **cambio** sin
riego futuro y se suma a la lectura base (`models.training.predict_level`); el resultado se recorta a 0 a
100. Las variables son las del entrenamiento y salen de las **mismas funciones**: la estación de
`training_table.seasonal_features` y la meteorología de `features.add_weather_features`; la lectura base y
los cambios previos se calculan igual que `training_table.dawn_snapshots` y `build_training_table`, pero
sobre el índice 0 a 100 que guarda `sensor_reading` (lineal, así que la media del índice es el índice de la
frecuencia media salvo recortes en los extremos).

Degradación en escalones (decidida por Laia el 23 de octubre de 2026; el modelo nunca detiene al motor).
La edad de una lectura es el tiempo entre ella y el origen:

1. Lectura de hace 36 horas o menos, lectura del amanecer del origen, meteorología completa y modelo
   disponible: `method = model`, `quality = validated`.
2. Lectura de hace 36 horas o menos pero sin lectura del amanecer, o con algún dato meteorológico
   ausente, o sin modelo para ese horizonte: `persistence_fallback`, `provisional`, con su limitación.
3. Lectura de entre 36 y 72 horas: `persistence_fallback`, `provisional`, con `stale_reading`.
4. Sin lectura, o de más de 72 horas (3 días): `insufficient_data`, sin valor previsto (nulo).

Persistencia = el nivel base sin cambio. Su base es la lectura del amanecer del origen si existe (igual que
la persistencia con la que se midió el modelo) y, si no, la última lectura válida. `limitations` es una
lista de objetos `{"code": ..., ...}`:

- `stale_reading` {age_hours, threshold_hours}: la lectura es más vieja de lo debido.
- `no_reading`: la sonda no tiene ninguna lectura válida.
- `no_dawn_reading`: no hay lectura en la ventana del origen (04:45 a 05:15 UTC).
- `weather_missing` {variables}: falta algún dato meteorológico del periodo.
- `model_unavailable`: no hay modelo para ese horizonte.
- `clipped_to_range` {raw_value}: el valor salía de 0 a 100 y se recortó.
- `depth_not_in_training` {depth_cm}: la profundidad de la sonda no estaba en el entrenamiento; se usa el modelo
  igualmente pero la predicción es `provisional` (solo si el modelo registra sus profundidades de entrenamiento).

Previsión que hay que pedir: 8 días (antelaciones 0 a 7). El modelo solo usa los días de origen a
origen+6 (horizonte 7), pero el motor de riego usa la meteorología de origen+1 a origen+7 (contrato C2).
`FORECAST_DAYS` lo deja escrito para la tarea 8.1.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Mapping

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from irrigation_app.features import WEATHER_FEATURE_COLUMNS, add_weather_features
from irrigation_app.models.registry import LoadedModel
from irrigation_app.models.training import FEATURE_COLUMNS, predict_change, predict_level
from irrigation_app.training_table import DAWN_WINDOW_MINUTES, HORIZONS, ORIGIN_HOUR_UTC, seasonal_features

FRESH_READING_HOURS = 36  # hasta aquí, la lectura es utilizable por el modelo (valor inicial, a ajustar con datos)
MAX_READING_AGE_HOURS = 72  # 3 días: pasado esto, insufficient_data
FORECAST_DAYS = 8  # antelaciones 0 a 7: el motor usa origen+1 a origen+7
LOOKBACK_DAYS = 3  # change_3d necesita el amanecer de origen-3

METHOD_MODEL, METHOD_FALLBACK = "model", "persistence_fallback"
VALIDATED, PROVISIONAL, INSUFFICIENT = "validated", "provisional", "insufficient_data"

PREDICTION_COLUMNS = [
    "sensor_id", "depth_cm", "origin_at", "horizon_days", "moisture_pred", "base_moisture",
    "base_reading_at", "method", "quality", "limitations", "model_id",
]
READING_COLUMNS = ["sensor_id", "depth_cm", "timestamp", "value"]
WEATHER_COLUMNS = ["day", "lead_days", "precipitation_mm", "et0_mm", "temperature_c", "humidity_pct", "wind_kmh"]

# La meteorología pertenece a una sola parcela: la unión de features.py necesita una clave de unidad y de ubicación.
_UNIT, _LOCATION = 0, "plot"


def origin_timestamp(origin_day: dt.date | str | pd.Timestamp) -> pd.Timestamp:
    """Las 05:00 UTC del día de origen."""
    day = pd.Timestamp(origin_day)
    day = day.tz_localize("UTC") if day.tzinfo is None else day.tz_convert("UTC")
    return day.floor("D") + pd.Timedelta(hours=ORIGIN_HOUR_UTC)


# --- Lecturas ------------------------------------------------------------------------------------

def _prepare_readings(readings: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in READING_COLUMNS if c not in readings.columns]
    if missing:
        raise ValueError(f"Faltan columnas en las lecturas: {', '.join(missing)}")
    frame = readings[READING_COLUMNS].dropna(subset=["sensor_id", "timestamp", "value"]).copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["value"] = frame["value"].astype(float)
    return frame


def dawn_index_snapshots(
    readings: pd.DataFrame,
    origin_hour: int = ORIGIN_HOUR_UTC,
    window_minutes: int = DAWN_WINDOW_MINUTES,
) -> pd.DataFrame:
    """Una fila por sonda y día: el índice medio de las lecturas a menos de `window_minutes` de la hora de origen.

    Es `training_table.dawn_snapshots` sobre el índice 0 a 100. Columnas: `sensor_id`, `depth_cm`, `day`,
    `base_index` y `base_reading_at` (la última lectura de la ventana).
    """
    frame = _prepare_readings(readings)
    timestamp = frame["timestamp"]
    distance = (timestamp.dt.hour * 60 + timestamp.dt.minute - origin_hour * 60).abs()
    near = frame[distance <= window_minutes]
    return (
        near.assign(day=near["timestamp"].dt.floor("D"))
        .groupby(["sensor_id", "depth_cm", "day"], as_index=False)
        .agg(base_index=("value", "mean"), base_reading_at=("timestamp", "max"))
    )


def last_readings(readings: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """La última lectura de cada sonda hasta `cutoff` (inclusive): `sensor_id`, `last_value` y `last_at`."""
    frame = _prepare_readings(readings)
    frame = frame[frame["timestamp"] <= cutoff].sort_values("timestamp")
    last = frame.groupby("sensor_id", as_index=False).tail(1)
    return last.rename(columns={"value": "last_value", "timestamp": "last_at"})[["sensor_id", "last_value", "last_at"]]


# --- Meteorología --------------------------------------------------------------------------------

def weather_from_forecast_data(rows: pd.DataFrame) -> pd.DataFrame:
    """Pasa filas de `forecast_data` (contrato C3) al formato que usa `features.py`.

    `rows` trae `fecha`, `lead_days`, `rainmm`, `eto`, `tmedia`, `rhmedia` y `vientomedia`.
    """
    renamed = rows.rename(
        columns={
            "fecha": "day", "rainmm": "precipitation_mm", "eto": "et0_mm", "tmedia": "temperature_c",
            "rhmedia": "humidity_pct", "vientomedia": "wind_kmh",
        }
    )
    out = renamed[WEATHER_COLUMNS].copy()
    out["day"] = pd.to_datetime(out["day"], utc=True)
    for column in WEATHER_COLUMNS[2:]:
        out[column] = out[column].astype(float)
    return out


def weather_for_origin(previous_runs: pd.DataFrame, location_id: str, origin_day: dt.date | str | pd.Timestamp) -> pd.DataFrame:
    """Reproduce la previsión que habría el día de origen a partir de las previsiones archivadas (Previous Runs).

    Para cada día origen+k toma la fila con antelación k, que es lo que existiría en producción, y nada
    posterior. Sirve para repetir un día pasado con `--as-of`.
    """
    origin = pd.Timestamp(origin_day)
    origin = origin.tz_localize("UTC") if origin.tzinfo is None else origin.tz_convert("UTC")
    origin = origin.floor("D")
    days = pd.to_datetime(previous_runs["day"], utc=True)
    selected = previous_runs[
        (previous_runs["location_id"] == location_id)
        & (previous_runs["lead_days"] == ((days - origin) / pd.Timedelta(days=1)))
        & (previous_runs["lead_days"] >= 0)
    ]
    return selected[WEATHER_COLUMNS].reset_index(drop=True)


# --- Variables de cada ejemplo -------------------------------------------------------------------

def build_feature_frame(
    sensors: pd.DataFrame,
    readings: pd.DataFrame,
    origin_day: dt.date | str | pd.Timestamp,
    weather: pd.DataFrame,
    horizons: tuple[int, ...] = HORIZONS,
) -> pd.DataFrame:
    """Una fila por sonda con lectura del amanecer y horizonte, con las variables del entrenamiento.

    Columnas: las de `FEATURE_COLUMNS`, más `sensor_id`, `origin_day`, `horizon_days` y
    `weather_incomplete`. Es lo que ve el modelo; una prueba comprueba que coincide con la tabla de
    entrenamiento del mismo ejemplo. Una sonda sin lectura del amanecer del origen no aparece.
    """
    origin = origin_timestamp(origin_day).floor("D")
    snapshots = dawn_index_snapshots(readings)
    index = snapshots.set_index(["sensor_id", "day"])["base_index"]

    today = snapshots[snapshots["day"] == origin][["sensor_id", "base_index"]]
    base = sensors[["sensor_id", "depth_cm"]].drop_duplicates("sensor_id").merge(today, on="sensor_id", how="inner")
    for column, back in (("change_1d", 1), ("change_3d", 3)):
        wanted = pd.MultiIndex.from_arrays([base["sensor_id"], pd.Series(origin - pd.Timedelta(days=back), index=base.index)])
        base[column] = base["base_index"].to_numpy() - index.reindex(wanted).to_numpy()
    base["origin_day"] = origin
    if base.empty:
        return pd.DataFrame(columns=["sensor_id", *FEATURE_COLUMNS, "origin_day", "horizon_days", "weather_incomplete"])

    examples = pd.concat([base.assign(horizon_days=h) for h in horizons], ignore_index=True)
    examples["unit_id"] = _UNIT
    examples[["doy_sin", "doy_cos"]] = seasonal_features(examples["origin_day"])

    weather = weather[WEATHER_COLUMNS].assign(location_id=_LOCATION)
    mapping = pd.DataFrame({"unit_id": [_UNIT], "location_id": [_LOCATION]})
    featured = add_weather_features(examples, weather, mapping)
    return featured[["sensor_id", *FEATURE_COLUMNS, "origin_day", "horizon_days", "weather_incomplete"]]


# --- Predicción ----------------------------------------------------------------------------------

def _round(value: float | None, digits: int) -> float | None:
    return None if value is None or pd.isna(value) else round(float(value), digits)


def _row(sensor: Mapping[str, Any], origin_at: pd.Timestamp, horizon: int, **fields: Any) -> dict[str, Any]:
    return {
        "sensor_id": int(sensor["sensor_id"]), "depth_cm": int(sensor["depth_cm"]), "origin_at": origin_at,
        "horizon_days": horizon, "moisture_pred": None, "base_moisture": None, "base_reading_at": None,
        "method": None, "quality": INSUFFICIENT, "limitations": [], "model_id": None, **fields,
    }


def _clip(raw: float, limitations: list[dict[str, Any]]) -> float:
    value = min(max(float(raw), 0.0), 100.0)
    if value != raw:
        limitations.append({"code": "clipped_to_range", "raw_value": _round(raw, 3)})
    return round(value, 3)


def predict_origin(
    sensors: pd.DataFrame,
    readings: pd.DataFrame,
    origin_day: dt.date | str | pd.Timestamp,
    weather: pd.DataFrame,
    models: Mapping[int, LoadedModel],
    *,
    fresh_hours: float = FRESH_READING_HOURS,
    max_age_hours: float = MAX_READING_AGE_HOURS,
    horizons: tuple[int, ...] = HORIZONS,
) -> pd.DataFrame:
    """Predicciones de todas las sondas y horizontes para un origen (contrato C1), sin tocar la base de datos.

    `sensors`: `sensor_id` y `depth_cm` de cada sonda de la parcela. `readings`: `sensor_id`, `depth_cm`,
    `timestamp` y `value` (índice 0 a 100; solo lecturas válidas); se ignora lo posterior al origen más la
    ventana del amanecer. `weather`: la previsión del día de origen (formato de `features.py`, ver
    `weather_from_forecast_data` y `weather_for_origin`). `models`: un `LoadedModel` por horizonte; si falta
    alguno, ese horizonte usa persistencia. Devuelve una fila por sonda y horizonte con `PREDICTION_COLUMNS`.
    """
    origin_at = origin_timestamp(origin_day)
    known_until = origin_at + pd.Timedelta(minutes=DAWN_WINDOW_MINUTES)  # lo que se sabía en el origen
    prepared = _prepare_readings(readings)
    prepared = prepared[prepared["timestamp"] <= known_until]

    last = last_readings(prepared, known_until).set_index("sensor_id")
    features = build_feature_frame(sensors, prepared, origin_at, weather, horizons)
    dawn = dawn_index_snapshots(prepared)
    dawn = dawn[dawn["day"] == origin_at.floor("D")].set_index("sensor_id")

    predicted: dict[tuple[int, int], float] = {}  # (sensor, horizonte) -> cambio previsto
    for horizon in horizons:
        loaded = models.get(horizon)
        rows = features[(features["horizon_days"] == horizon) & ~features["weather_incomplete"]]
        if loaded is None or rows.empty:
            continue
        change = predict_change(loaded.model, rows, loaded.features)
        predicted.update(zip(zip(rows["sensor_id"].astype(int), [horizon] * len(rows)), change))
    incomplete = {
        (int(r.sensor_id), int(r.horizon_days)): [c for c in WEATHER_FEATURE_COLUMNS if pd.isna(getattr(r, c))]
        for r in features[features["weather_incomplete"]].itertuples()
    }

    out = []
    for sensor in sensors.drop_duplicates("sensor_id").to_dict("records"):
        sid = int(sensor["sensor_id"])
        has_last = sid in last.index
        last_at = last.loc[sid, "last_at"] if has_last else None
        last_value = float(last.loc[sid, "last_value"]) if has_last else None
        age_hours = max(0.0, (origin_at - last_at) / pd.Timedelta(hours=1)) if has_last else None
        has_dawn = sid in dawn.index

        for horizon in horizons:
            model_id = models[horizon].model_id if horizon in models else None
            if not has_last or age_hours > max_age_hours:
                limitation = (
                    {"code": "no_reading"} if not has_last
                    else {"code": "stale_reading", "age_hours": _round(age_hours, 1), "threshold_hours": max_age_hours}
                )
                out.append(_row(
                    sensor, origin_at, horizon, base_moisture=_round(last_value, 4), base_reading_at=last_at,
                    limitations=[limitation], model_id=model_id,
                ))
                continue

            limitations: list[dict[str, Any]] = []
            base_value = float(dawn.loc[sid, "base_index"]) if has_dawn else last_value
            base_at = dawn.loc[sid, "base_reading_at"] if has_dawn else last_at
            key = (sid, horizon)
            if age_hours > fresh_hours:
                limitations.append({"code": "stale_reading", "age_hours": _round(age_hours, 1), "threshold_hours": fresh_hours})
            if not has_dawn:
                limitations.append({"code": "no_dawn_reading"})
            if key in incomplete:
                limitations.append({"code": "weather_missing", "variables": incomplete[key]})
            if horizon not in models:
                limitations.append({"code": "model_unavailable"})

            if limitations:  # cualquier causa de las anteriores deja la predicción en persistencia
                raw, method, quality = base_value, METHOD_FALLBACK, PROVISIONAL
            else:
                raw = float(predict_level([base_value], [predicted[key]])[0])
                method, quality = METHOD_MODEL, VALIDATED
                seen = models[horizon].training_depths
                if seen is not None and int(sensor["depth_cm"]) not in seen:
                    # El modelo se usa fuera de lo visto al entrenar: se usa igualmente, pero provisional.
                    quality = PROVISIONAL
                    limitations.append({"code": "depth_not_in_training", "depth_cm": int(sensor["depth_cm"])})
            pred = _clip(raw, limitations)
            out.append(_row(
                sensor, origin_at, horizon, moisture_pred=pred, base_moisture=_round(base_value, 4),
                base_reading_at=base_at, method=method, quality=quality, limitations=limitations, model_id=model_id,
            ))
    return pd.DataFrame(out, columns=PREDICTION_COLUMNS)


# --- Base de datos -------------------------------------------------------------------------------

def load_plot_inputs(connection: psycopg.Connection, plot_id: int, origin_day: dt.date | str | pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sondas activas de la parcela y sus lecturas válidas necesarias para un origen.

    Devuelve `(sensors, readings)`: las lecturas de los días previos que piden los cambios de 1 y 3 días,
    más la última lectura de cada sonda antes del origen aunque sea más vieja (para medir su antigüedad).
    """
    origin_at = origin_timestamp(origin_day)
    known_until = origin_at + pd.Timedelta(minutes=DAWN_WINDOW_MINUTES)
    since = origin_at - pd.Timedelta(days=LOOKBACK_DAYS, hours=1)
    sensors = pd.DataFrame(
        connection.execute(
            "SELECT sensor_id, depth_cm FROM sensor WHERE plot_id = %s AND is_active ORDER BY depth_cm", (plot_id,)
        ).fetchall(),
        columns=["sensor_id", "depth_cm"],
    )
    rows = connection.execute(
        "(SELECT r.sensor_id, s.depth_cm, r.measured_at, r.value FROM sensor_reading r "
        "JOIN sensor s USING (sensor_id) "
        "WHERE s.plot_id = %s AND s.is_active AND r.quality_flag = 'ok' AND r.measured_at > %s AND r.measured_at <= %s) "
        "UNION ALL "
        "(SELECT DISTINCT ON (r.sensor_id) r.sensor_id, s.depth_cm, r.measured_at, r.value FROM sensor_reading r "
        "JOIN sensor s USING (sensor_id) "
        "WHERE s.plot_id = %s AND s.is_active AND r.quality_flag = 'ok' AND r.measured_at <= %s "
        "ORDER BY r.sensor_id, r.measured_at DESC)",
        (plot_id, since, known_until, plot_id, known_until),
    ).fetchall()
    readings = pd.DataFrame(rows, columns=READING_COLUMNS).drop_duplicates(["sensor_id", "timestamp"])
    readings["value"] = readings["value"].astype(float)
    return sensors, readings


def _none_if_nan(value: Any) -> Any:
    """Un número ausente (NaN de pandas) es None en la base: nunca un NaN guardado como número."""
    return None if value is None or pd.isna(value) else value


def store_predictions(connection: psycopg.Connection, predictions: pd.DataFrame) -> int:
    """Guarda las predicciones en `ml_prediction`. Devuelve cuántas filas nuevas entraron.

    Es idempotente: el origen ya guardado no se toca (la tabla es inmutable), así que repetir el cálculo
    no duplica ni cambia nada. Todo entra en una transacción.
    """
    values = [
        (
            int(r.sensor_id), r.origin_at.to_pydatetime(), int(r.horizon_days), _none_if_nan(r.moisture_pred),
            _none_if_nan(r.base_moisture),
            None if pd.isna(r.base_reading_at) else r.base_reading_at.to_pydatetime(), _none_if_nan(r.method), r.quality,
            Jsonb(list(r.limitations)), None if pd.isna(r.model_id) else int(r.model_id),
        )
        for r in predictions.itertuples()
    ]
    inserted = 0
    with connection.transaction(), connection.cursor() as cursor:
        for row in values:
            cursor.execute(
                "INSERT INTO ml_prediction (sensor_id, origin_at, horizon_days, moisture_pred, base_moisture, "
                "base_reading_at, method, quality, limitations, model_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (sensor_id, origin_at, horizon_days) DO NOTHING",
                row,
            )
            inserted += cursor.rowcount
    return inserted


def predict_plot(
    connection: psycopg.Connection,
    plot_id: int,
    origin_day: dt.date | str | pd.Timestamp,
    weather: pd.DataFrame,
    models: Mapping[int, LoadedModel],
) -> pd.DataFrame:
    """Lee las lecturas de la parcela, predice y escribe en `ml_prediction`. Devuelve las predicciones calculadas."""
    sensors, readings = load_plot_inputs(connection, plot_id, origin_day)
    predictions = predict_origin(sensors, readings, origin_day, weather, models)
    store_predictions(connection, predictions)
    return predictions
