"""Predicción diaria de la parcela de referencia en local (tarea 8.2).

Uso (desde la raíz del proyecto):
    .venv/Scripts/python scripts/run_daily.py --env-file .env.docker-pg                       # hoy
    .venv/Scripts/python scripts/run_daily.py --env-file .env.docker-pg --as-of 2026-08-25    # reproducir un día pasado
    .venv/Scripts/python scripts/run_daily.py --env-file .env.docker-pg --as-of 2026-08-25 --skip-readings

Qué hace, en orden, para el origen de las 05:00 UTC del día `--as-of` (por defecto, hoy en UTC):
1. Avisa si los 7 días a recomendar (origen+1 a origen+7) caen fuera de la campaña de riego del cultivo.
2. Carga las lecturas nuevas de la parcela desde el export local (`data/silver/readings.parquet`), sin duplicar
   (compara todo: unas 226.000 lecturas tardan unos 5 segundos, y así recoge también huecos antiguos).
3. Obtiene la previsión meteorológica de 8 días (antelaciones 0 a 7) y la guarda en `forecast_data`:
   la actual de Open-Meteo si el origen es hoy, o las previsiones archivadas (Previous Runs) del día
   `--as-of` si es pasado. Si esa previsión ya estaba guardada completa, no se vuelve a pedir.
4. Predice la humedad de cada sonda a 1 a 7 días con el modelo activo y la escribe en `ml_prediction`.
   `ml_prediction` es inmutable: repetir el mismo origen no cambia ni duplica nada (si lo recalculado
   difiere de lo guardado, se avisa y se conserva lo guardado).
5. Deja lo que necesita el motor de riego (`models/engine_feed.py`) y registra la ejecución en `job_run`.
6. Hueco de las alertas (tarea 8.4): NO implementado; se decide el 2 de diciembre.

Degradación (decidida por Laia el 23 de octubre de 2026): lectura de hasta 36 h = modelo; de 36 a 72 h =
persistencia provisional; más de 72 h o sin lectura = `insufficient_data` (sin valor). El modelo nunca
detiene al motor. Si falta la previsión que el modelo necesita (red caída, respuesta incompleta o un 429), las
predicciones se escriben igualmente con persistencia (`persistence_fallback`, `provisional`, limitación
`weather_missing`), la ejecución queda `partial` en `job_run` con el motivo y el aviso sale por pantalla
(código de salida 0). No se insiste tras un 429. Ojo: `ml_prediction` es inmutable, así que esa predicción
degradada queda fija para ese origen aunque luego vuelva la red.

Solo escribe en un servidor local sin `--allow-remote`. Nunca imprime la contraseña. Códigos de salida:
0 correcto o parcial, 1 fallo, 2 argumentos o uso incorrecto.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import load_reference_plot as lrp  # noqa: E402
from irrigation_app import db  # noqa: E402
from irrigation_app.data_layers import READINGS_PATH, UNITS_PATH  # noqa: E402
from irrigation_app.irrigation_engine import reference_values as rv  # noqa: E402
from irrigation_app.models import inference  # noqa: E402
from irrigation_app.models.engine_feed import load_engine_feed  # noqa: E402
from irrigation_app.models.registry import load_active_models  # noqa: E402
from irrigation_app.reference_plot import REFERENCE_PLOT, ReferencePlot  # noqa: E402
from irrigation_app.training_table import DAWN_WINDOW_MINUTES  # noqa: E402
from irrigation_app.weather import forecast_store as fs  # noqa: E402
from irrigation_app.weather.open_meteo import FORECAST_DAYS, WeatherError, fetch_forecast, fetch_previous_runs  # noqa: E402

JOB_NAME = "run_daily"
STALE_JOB_HOURS = 1  # un `running` más viejo que esto se da por muerto (un cierre brusco) y no bloquea
LOCAL_HOSTS = lrp.LOCAL_HOSTS
FORECAST_MODES = ("auto", "current", "archived")


class UsageError(ValueError):
    """Un argumento o un estado que impide empezar (código de salida 2); no se registra en `job_run`."""


# --- Fechas y campaña --------------------------------------------------------------------------------

def validate_as_of(as_of: dt.date, now: dt.datetime) -> None:
    """El origen debe haber ocurrido ya: las 05:00 UTC del día más la ventana del amanecer (15 minutos)."""
    ready = inference.origin_timestamp(as_of) + pd.Timedelta(minutes=DAWN_WINDOW_MINUTES)
    if pd.Timestamp(now) < ready:
        raise UsageError(
            f"El origen del {as_of.isoformat()} (05:00 UTC) todavía no ha ocurrido o no ha cerrado su ventana de lectura "
            f"(hasta las {ready.strftime('%H:%M')} UTC). Usa una fecha anterior."
        )


def choose_forecast_source(as_of: dt.date, today: dt.date, mode: str) -> str:
    """`current` solo vale para hoy (la previsión actual de otro día tendría otra antelación); `auto` elige solo."""
    if mode not in FORECAST_MODES:
        raise UsageError(f"--forecast debe ser uno de {', '.join(FORECAST_MODES)}")
    if mode == "current" and as_of != today:
        raise UsageError("--forecast current solo sirve para hoy; para un día pasado usa archived (Previous Runs)")
    if mode == "archived" or (mode == "auto" and as_of != today):
        return fs.SOURCE_ARCHIVED
    return fs.SOURCE_CURRENT


def campaign_warning(plot: ReferencePlot, origin: dt.date) -> str | None:
    """Aviso si alguno de los 7 días a recomendar cae fuera del ciclo del cultivo (campaña); None si todos caen dentro.

    Usa el mismo criterio que el motor (`reference_values.stage_at` sobre el día del ciclo, con el inicio de la
    parcela o el supuesto del 1 de marzo). El script sigue y escribe las predicciones: solo avisa.
    """
    if plot.crop is None:
        return None
    crop_name = plot.crop.value
    cycle = plot.cycle_start_date.value if plot.cycle_start_date else None
    start = (cycle.month, cycle.day) if cycle else rv.ASSUMED_CYCLE_START
    days = [origin + dt.timedelta(days=k) for k in range(1, 8)]
    outside = [d for d in days if rv.stage_at(crop_name, rv.day_of_cycle(d, start)) is None]
    if not outside:
        return None
    first = rv.cycle_start_in_year(start[0], start[1], origin.year)
    last = first + dt.timedelta(days=sum(s.days for s in rv.crop(crop_name).stage_days) - 1)
    window = f"campaña del {first.strftime('%d-%m')} al {last.strftime('%d-%m')}"
    if len(outside) == 7:
        return (
            f"Aviso: los 7 días a recomendar ({days[0]} a {days[-1]}) caen FUERA de la {window}: el motor dará "
            "'do_not_irrigate' (out_of_season). Las predicciones de humedad se calculan igual."
        )
    return f"Aviso: {len(outside)} de los 7 días a recomendar caen fuera de la {window} ({', '.join(str(d) for d in outside)})."


# --- job_run ----------------------------------------------------------------------------------------

def start_job(connection: psycopg.Connection, triggered_by: str, now: dt.datetime) -> int:
    """Crea la fila `running` de `job_run`. Si hay otra ejecución en marcha reciente, se niega (`UsageError`)."""
    with connection.transaction():
        busy = connection.execute(
            "SELECT job_run_id, started_at FROM job_run WHERE job_name = %s AND status = 'running' AND started_at > %s",
            (JOB_NAME, now - dt.timedelta(hours=STALE_JOB_HOURS)),
        ).fetchone()
        if busy:
            raise UsageError(f"Ya hay una ejecución en marcha (job_run {busy[0]}, empezó {busy[1]:%Y-%m-%d %H:%M} UTC).")
        return int(
            connection.execute(
                "INSERT INTO job_run (job_name, triggered_by, started_at, status) VALUES (%s, %s, %s, 'running') RETURNING job_run_id",
                (JOB_NAME, triggered_by, now),
            ).fetchone()[0]
        )


def finish_job(
    connection: psycopg.Connection, job_id: int, status: str, ok: int, failed: int, error: str | None, details: dict[str, Any]
) -> None:
    with connection.transaction():
        connection.execute(
            "UPDATE job_run SET finished_at = now(), status = %s, plots_ok = %s, plots_failed = %s, error_summary = %s, details = %s "
            "WHERE job_run_id = %s",
            (status, ok, failed, error, Jsonb(details, dumps=lambda obj: json.dumps(obj, default=str)), job_id),
        )


# --- Pasos ------------------------------------------------------------------------------------------

def find_plot(connection: psycopg.Connection, plot: ReferencePlot, plot_code: str | None) -> dict[str, Any]:
    """La parcela por código, o por el nombre de la de referencia si no se da. Debe haber exactamente una."""
    if plot_code:
        rows = connection.execute(
            "SELECT plot_id, latitude, longitude, plot_code FROM plot WHERE plot_code = %s", (plot_code.upper(),)
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT plot_id, latitude, longitude, plot_code FROM plot WHERE name = %s", (plot.name,)
        ).fetchall()
    if len(rows) != 1:
        raise UsageError(
            f"Se esperaba una parcela y hay {len(rows)}"
            + (" (carga la de referencia con scripts/load_reference_plot.py)" if not rows else ": indica --plot-code")
        )
    plot_id, latitude, longitude, code = rows[0]
    return {"plot_id": int(plot_id), "latitude": float(latitude), "longitude": float(longitude), "plot_code": code}


def load_new_readings(
    connection: psycopg.Connection, plot_id: int, plot: ReferencePlot, readings_path: Path, units_path: Path
) -> dict[str, Any]:
    """Carga del export local las lecturas que faltan (idempotente, con las salvaguardas de `load_reference_plot.py`)."""
    for path in (readings_path, units_path):
        if not path.exists():
            raise UsageError(f"No existe el archivo {path} (usa --skip-readings para no cargar lecturas)")
    units = pd.read_parquet(units_path)
    unit = units[units["unit_id"] == plot.unit_id]
    if unit.empty:
        raise UsageError(f"La unidad {plot.unit_id} no está en {units_path.name}")
    raw = pd.read_parquet(readings_path, filters=[("unit_id", "==", plot.unit_id)])
    rows = lrp.build_reading_rows(raw, unit.iloc[0], plot.sensor_depths_cm)
    with connection.transaction():
        sensors = lrp.ensure_sensors(connection, plot_id, plot)
        inserted = lrp.insert_readings(connection, rows, sensors) if len(rows) else 0
    newest = connection.execute(
        "SELECT max(r.measured_at) FROM sensor_reading r JOIN sensor s USING (sensor_id) WHERE s.plot_id = %s", (plot_id,)
    ).fetchone()[0]
    return {"examined": len(rows), "inserted": inserted, "latest_reading": newest}


def obtain_forecast(
    connection: psycopg.Connection,
    plot_id: int,
    latitude: float,
    longitude: float,
    origin: dt.date,
    source: str,
    now: dt.datetime,
    fetch_current: Callable[..., pd.DataFrame] = fetch_forecast,
    fetch_archived: Callable[..., pd.DataFrame] = fetch_previous_runs,
) -> dict[str, Any]:
    """Deja en `forecast_data` la previsión del origen y devuelve qué pasó. No lanza por un fallo de red.

    Si la previsión ya está completa (8 días), no se pide nada. Un fallo de Open-Meteo (red caída, 429,
    respuesta sin las variables) se anota en `error` y se sigue con lo que haya guardado.
    """
    leads = tuple(range(FORECAST_DAYS))
    result: dict[str, Any] = {"source": source, "fetched": False, "stored": 0, "error": None}
    if not fs.missing_weather(fs.load_forecast(connection, plot_id, origin, source), leads):
        return result
    try:
        if source == fs.SOURCE_CURRENT:
            daily = fetch_current(latitude, longitude, FORECAST_DAYS)
        else:
            daily = fetch_archived(latitude, longitude, origin, origin + dt.timedelta(days=FORECAST_DAYS - 1), lead_days=leads)
        result["fetched"] = True
        result["stored"] = fs.store_forecast(connection, plot_id, daily, origin, source, now)
    except WeatherError as error:  # incluye RateLimitedError: no se insiste
        result["error"] = f"{type(error).__name__}: {error}"
    return result


def predict_and_store(
    connection: psycopg.Connection, plot_id: int, origin: dt.date, forecast: pd.DataFrame, models: dict
) -> dict[str, Any]:
    """Predice, escribe en `ml_prediction` y compara lo recalculado con lo que había guardado."""
    weather = inference.weather_from_forecast_data(forecast)
    sensors, readings = inference.load_plot_inputs(connection, plot_id, origin)
    computed = inference.predict_origin(sensors, readings, origin, weather, models)
    inserted = inference.store_predictions(connection, computed)

    feed = load_engine_feed(connection, plot_id, origin)
    stored = {(p["depth_cm"], p["horizon_days"]): p for p in feed["predictions"]}
    differs = 0
    for r in computed.itertuples():
        old = stored.get((int(r.depth_cm), int(r.horizon_days)))
        if old is None:
            continue
        same_value = (pd.isna(r.moisture_pred) and old["moisture_pred"] is None) or (
            not pd.isna(r.moisture_pred) and old["moisture_pred"] is not None and abs(float(r.moisture_pred) - old["moisture_pred"]) < 1e-3
        )
        if not (same_value and r.quality == old["quality"]):
            differs += 1
    qualities = pd.Series([p["quality"] for p in feed["predictions"]]).value_counts().to_dict()
    methods = pd.Series([p["method"] or "none" for p in feed["predictions"]]).value_counts().to_dict()
    return {
        "computed": len(computed), "inserted": inserted, "already_stored": len(computed) - inserted,
        "differs_from_stored": differs, "by_quality": qualities, "by_method": methods, "feed": feed,
        "models_active": sorted(models),
    }


def alerts_step(connection: psycopg.Connection, plot_id: int, origin: dt.date) -> dict[str, str]:
    """HUECO de las alertas agronómicas (tarea 8.4): «lluvia fuerte prevista» y «suelo muy seco» en `alert`.

    NO está implementado a propósito: se decide si entra o no el 2 de diciembre de 2026, según el tiempo que
    quede, y los umbrales los acuerda Laia con AgronomoFAO56. Cuando entre, irá aquí: leer `load_engine_feed`,
    crear la alerta si no hay una activa del mismo tipo y resolverla cuando deje de cumplirse.
    """
    return {"status": "not_implemented", "task": "8.4"}


# --- Orquestación -------------------------------------------------------------------------------------

def run_daily(
    connection: psycopg.Connection,
    *,
    as_of: dt.date | None = None,
    now: dt.datetime | None = None,
    forecast_mode: str = "auto",
    plot_code: str | None = None,
    readings_path: Path = READINGS_PATH,
    units_path: Path = UNITS_PATH,
    skip_readings: bool = False,
    triggered_by: str = "manual",
    models: dict | None = None,
    fetch_current: Callable[..., pd.DataFrame] = fetch_forecast,
    fetch_archived: Callable[..., pd.DataFrame] = fetch_previous_runs,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Una ejecución completa. Devuelve `{"status", "details", "error"}`; `UsageError` si no se puede ni empezar.

    `connection` debe abrirse con `autocommit=True` (cada paso abre su propia transacción).
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(dt.timezone.utc).date()
    origin = as_of or today
    validate_as_of(origin, now)
    source = choose_forecast_source(origin, today, forecast_mode)
    plot = REFERENCE_PLOT

    job_id = start_job(connection, triggered_by, now)
    details: dict[str, Any] = {"origin_day": origin, "forecast_source": source}
    status, error = "failed", None
    try:
        info = find_plot(connection, plot, plot_code)
        plot_id = info["plot_id"]
        details["plot_id"] = plot_id
        log(f"Origen: {origin} 05:00 UTC; parcela {plot.name} (id {plot_id}); previsión: {source}.")

        warning = campaign_warning(plot, origin)
        details["campaign_warning"] = warning
        if warning:
            log(warning)

        if skip_readings:
            details["readings"] = {"skipped": True}
        else:
            details["readings"] = load_new_readings(connection, plot_id, plot, readings_path, units_path)
            log(f"Lecturas: {details['readings']['inserted']:,} nuevas; la última guardada es del {details['readings']['latest_reading']}.")

        details["forecast"] = obtain_forecast(
            connection, plot_id, info["latitude"], info["longitude"], origin, source, now, fetch_current, fetch_archived
        )
        forecast = fs.load_forecast(connection, plot_id, origin, source)
        model_gaps = fs.missing_weather(forecast, fs.MODEL_LEADS)
        engine_gaps = fs.missing_weather(forecast, fs.ENGINE_LEADS, fs.ENGINE_COLUMNS)
        details["forecast"].update(model_gaps={str(k): v for k, v in model_gaps.items()}, engine_gaps={str(k): v for k, v in engine_gaps.items()})
        fetch_error = details["forecast"]["error"]
        log(
            f"Previsión: {len(forecast)} de {FORECAST_DAYS} días guardados"
            + (f" ({details['forecast']['stored']} filas nuevas o completadas)" if details["forecast"]["fetched"] else " (ya estaba completa)" if not model_gaps and not engine_gaps else "")
            + (f"; error de Open-Meteo: {fetch_error}" if fetch_error else "")
            + "."
        )

        loaded = models if models is not None else load_active_models(connection)
        if not loaded:
            log("Aviso: no hay ningún modelo activo en ml_model; se escribe persistencia (limitación model_unavailable).")
        outcome = predict_and_store(connection, plot_id, origin, forecast, loaded)
        feed = outcome.pop("feed")
        details["predictions"] = outcome
        details["alerts"] = alerts_step(connection, plot_id, origin)  # hueco: 8.4 no implementada
        details["engine"] = {
            "predictions": len(feed["predictions"]), "forecast_days": [str(w["date"]) for w in feed["weather"]],
            "period_start": str(origin + dt.timedelta(days=1)), "period_end": str(origin + dt.timedelta(days=7)),
        }
        log(
            f"Predicciones: {outcome['computed']} calculadas, {outcome['inserted']} nuevas y {outcome['already_stored']} ya guardadas "
            f"(no se modifican). Calidad: {outcome['by_quality']}; método: {outcome['by_method']}."
        )
        if outcome["differs_from_stored"]:
            log(
                f"Aviso: {outcome['differs_from_stored']} predicciones recalculadas ahora difieren de las guardadas (por ejemplo, "
                "llegaron lecturas después de la primera ejecución). ml_prediction es inmutable: se conservan las guardadas."
            )
        _print_table(feed["predictions"], log)

        problems = []
        if model_gaps:
            problems.append(
                "falta previsión para el modelo (antelaciones " + ", ".join(str(k) for k in model_gaps) + ")"
                + (f": {fetch_error}" if fetch_error else "")
                + "; escritas con persistencia provisional y weather_missing"
            )
        if engine_gaps:
            problems.append("falta ET0 o lluvia de algún día para el motor: " + ", ".join(f"origen+{k}" for k in engine_gaps))
        if any(q != "validated" for q in outcome["by_quality"]):
            problems.append("hay predicciones provisionales o sin datos suficientes (lecturas viejas o ausentes)")
        status = "partial" if problems else "success"
        error = "; ".join(problems) or None
        return _close(connection, job_id, status, 1, 0, error, details, log)
    except UsageError as usage:
        finish_job(connection, job_id, "failed", 0, 1, str(usage), details)
        raise
    except Exception as exception:  # el registro de la ejecución se cierra siempre
        error = f"{type(exception).__name__}: {exception}"
        return _close(connection, job_id, "failed", 0, 1, error, details, log)


def _close(connection, job_id, status, ok, failed, error, details, log) -> dict[str, Any]:
    finish_job(connection, job_id, status, ok, failed, error, details)
    log(f"Ejecución {job_id}: {status}" + (f" ({error})" if error else "") + ".")
    return {"status": status, "details": details, "error": error, "job_run_id": job_id}


def _print_table(predictions: list[dict[str, Any]], log: Callable[[str], None]) -> None:
    """Humedad prevista por profundidad y horizonte (lo guardado), con un `~` si es persistencia y `-` si no hay valor."""
    depths = sorted({p["depth_cm"] for p in predictions})
    by_key = {(p["depth_cm"], p["horizon_days"]): p for p in predictions}
    log("  Profundidad   base   " + "".join(f"  +{h}d " for h in range(1, 8)))
    for depth in depths:
        base = next((p["base_moisture"] for p in predictions if p["depth_cm"] == depth and p["base_moisture"] is not None), None)
        cells = []
        for h in range(1, 8):
            p = by_key.get((depth, h))
            if p is None or p["moisture_pred"] is None:
                cells.append("   -   ")
            else:
                cells.append(f"{p['moisture_pred']:6.1f}{'~' if p['method'] != 'model' else ' '}")
        log(f"  {depth:>7} cm  {'' if base is None else f'{base:5.1f}'}  " + "".join(cells))
    log("  (~ = persistencia provisional; - = sin valor, datos insuficientes)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", default=".env", help="archivo con las variables DB_* (por defecto .env)")
    parser.add_argument("--as-of", type=dt.date.fromisoformat, metavar="AAAA-MM-DD", help="día de origen (por defecto, hoy en UTC)")
    parser.add_argument("--forecast", choices=FORECAST_MODES, default="auto", help="auto: actual si es hoy, archivada si es pasado")
    parser.add_argument("--plot-code", help="código de la parcela (por defecto, la de referencia por su nombre)")
    parser.add_argument("--readings", type=Path, default=READINGS_PATH)
    parser.add_argument("--units", type=Path, default=UNITS_PATH)
    parser.add_argument("--skip-readings", action="store_true", help="no carga lecturas del export local")
    parser.add_argument("--scheduled", action="store_true", help="lo anota en job_run como tarea programada, no manual")
    parser.add_argument("--allow-remote", action="store_true", help="permite escribir en un servidor que no sea local (Aiven)")
    args = parser.parse_args()

    try:
        config = db.load_config(args.env_file)
    except db.DatabaseConfigError as error:
        sys.exit(str(error))
    print(f"Destino: {db.describe(config)}")
    if config.host not in LOCAL_HOSTS and not args.allow_remote:
        print("El destino no es local. Para escribir en Aiven hay que pasar --allow-remote (decisión de Laia).")
        sys.exit(2)
    try:
        connection = db.connect(config, autocommit=True)
    except psycopg.OperationalError as error:
        sys.exit(f"No se pudo conectar: {error}")
    with connection:
        try:
            result = run_daily(
                connection, as_of=args.as_of, forecast_mode=args.forecast, plot_code=args.plot_code,
                readings_path=args.readings, units_path=args.units, skip_readings=args.skip_readings,

                triggered_by="scheduled" if args.scheduled else "manual",
            )
        except UsageError as error:
            print(error)
            sys.exit(2)
    sys.exit(1 if result["status"] == "failed" else 0)


if __name__ == "__main__":
    main()
