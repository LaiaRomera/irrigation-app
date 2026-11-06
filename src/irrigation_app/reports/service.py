"""Servicio de informes (tarea 7.4): une la base de datos, el motor FAO-56, el texto y el PDF.

`generate_report(plot_id, user_id, report_type)` es lo único que llama la web. No entrena, no descarga datos y no
mira internet: lee de PostgreSQL lo que ya hay (`ml_prediction`, `forecast_data`, `ml_model`) y escribe una
`recommendation` (inmutable, con versiones) y un `report`.

Pasos:
1. **Permiso** en el servidor: la parcela debe existir, estar activa y ser de `user_id` (cuenta activa). Si no, el
   mismo error `AccessDenied` para «no existe» y «es de otra cuenta» (no se revela cuál). El código de parcela
   (`plot.plot_code`) solo sirve para elegir la parcela en la lista del usuario (`find_user_plot`); no da acceso.
2. **Origen**: el último origen con predicciones de humedad de las sondas de la parcela (`origin_date` lo fija).
   Si no hay ninguno, el resultado es `no_prediction`: la web no calcula nada.
3. **Cultivo y riego**: solo la parcela de referencia (unidad 1168, `REFERENCE_PLOT`) tiene datos de cultivo,
   suelo, riego y anclajes. Para cualquier otra, el resultado es `crop_data_unavailable`: no hay recomendación
   de riego, solo la humedad prevista por sonda (índice relativo de 0 a 100) y un mensaje claro. No se guarda
   nada en `report` (cuelga de una `recommendation`).
4. **Motor**: `EngineInput` con las predicciones, la meteorología de `forecast_data` (origen+1 a origen+7) y
   `plot.irrigation_interval_days` como intervalo de reposición (nulo = el motor supone 3 días).
5. **Recomendación**: si ya existe una con las mismas entradas y la misma versión del método, se reutiliza; si
   no, se crea una versión nueva enlazada a la anterior. Así, dos informes (LLM y plantilla) de la misma
   recomendación comparten `recommendation_id`.
6. **Texto**: LLM si hay generador y sus cifras coinciden con las del motor; si no, plantilla de respaldo con el
   motivo en `validation_checks`. Con el mismo `recommendation_id`, tipo, generador y versión no se duplica: se
   devuelve el informe que ya existe (abrir un informe no recalcula).

Un informe anterior al día de la consulta se marca como antiguo con su fecha (`ReportContent.is_stale`). `today`
no se guarda en el contenido: se aplica cada vez que se lee.

Los errores (`ReportError`) llevan un código y un mensaje fijo en español: nunca claves, rutas ni trazas.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from irrigation_app import db
from irrigation_app.irrigation_engine import EngineInput, EngineResult, SensorPrediction, WeatherDay, run_engine
from irrigation_app.reference_plot import REFERENCE_PLOT, ReferencePlot
from irrigation_app.reports.content import ReportContent, ReportType, build_report_content
from irrigation_app.reports.llm import TextGenerator, generate_text
from irrigation_app.reports.metrics import HorizonError, typical_error_from_model_metrics
from irrigation_app.reports.validation import validate_fields

logger = logging.getLogger(__name__)

REPORT_TYPES: tuple[str, ...] = ("daily", "weekly")
PLAN_DAYS = 7
Status = Literal["generated", "no_prediction", "crop_data_unavailable"]

MESSAGES = {
    "access_denied": "No tiene acceso a esa parcela.",
    "invalid_report_type": "El tipo de informe no es válido: debe ser diario o semanal.",
    "report_not_found": "No se encontró ese informe.",
    "engine_error": "No se pudo calcular la recomendación con los datos disponibles.",
    "database_error": "No se pudo acceder a los datos. Inténtelo de nuevo más tarde.",
    "pdf_unavailable": "No se pudo preparar el PDF en este equipo.",
}
NO_PREDICTION_MESSAGE = (
    "Todavía no hay predicción de humedad para esta parcela, así que no se puede preparar el informe. "
    "El informe no calcula nada al abrirse: espere a la siguiente actualización diaria."
)
CROP_DATA_MESSAGE = (
    "Datos de cultivo y de riego no disponibles para esta parcela: no se emite recomendación de riego. "
    "Solo se muestra la humedad prevista por sonda (índice relativo de 0 a 100)."
)


class ReportError(Exception):
    """Fallo del servicio. `code` es estable y `message` es un texto fijo y seguro para mostrar."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
        self.message = MESSAGES.get(code, "No se pudo completar la operación.")


class AccessDenied(ReportError):
    """La parcela (o el informe) no existe o no es de ese usuario. No se distingue una cosa de la otra."""

    def __init__(self) -> None:
        super().__init__("access_denied")


@dataclass(frozen=True)
class PlotInfo:
    """Una parcela del usuario, como la ve la web: sin coordenadas."""

    plot_id: int
    plot_code: str | None
    name: str
    timezone: str
    irrigation_interval_days: int | None
    crop: str | None = None


@dataclass(frozen=True)
class StoredReport:
    report_id: int
    recommendation_id: int
    report_type: ReportType
    generated_at: dt.datetime
    content: ReportContent  # con `today` aplicado
    generator: str
    generator_version: str
    validation_checks: dict[str, Any]
    created: bool  # False si ya existía y se devolvió tal cual
    recommendation_version: int
    recommendation_reused: bool  # True si la recomendación ya existía (mismas entradas)

    @property
    def origin_date(self) -> dt.date:
        return self.content.origin_date

    @property
    def is_stale(self) -> bool:
        return self.content.is_stale


@dataclass(frozen=True)
class ReportOutcome:
    status: Status
    plot: PlotInfo
    message: str = ""
    origin_date: dt.date | None = None
    report: StoredReport | None = None
    moisture_forecast: tuple[dict[str, Any], ...] = field(default=())  # solo en `crop_data_unavailable`


# --- Permisos y parcelas -------------------------------------------------------------------------

_PLOT_COLUMNS = "p.plot_id, p.plot_code, p.name, p.timezone, p.irrigation_interval_days, p.crop"


def _plot_info(row: tuple) -> PlotInfo:
    return PlotInfo(
        plot_id=int(row[0]), plot_code=row[1], name=row[2], timezone=row[3],
        irrigation_interval_days=None if row[4] is None else int(row[4]), crop=row[5],
    )


def get_user_plot(connection: psycopg.Connection, plot_id: int, user_id: int) -> PlotInfo:
    """La parcela, solo si es de ese usuario (activo) y está activa. Si no: `AccessDenied` (siempre el mismo error)."""
    row = connection.execute(
        f"SELECT {_PLOT_COLUMNS} FROM plot p JOIN app_user u ON u.user_id = p.user_id "
        "WHERE p.plot_id = %s AND p.user_id = %s AND p.is_active AND u.is_active",
        (plot_id, user_id),
    ).fetchone()
    if row is None:
        raise AccessDenied()
    return _plot_info(row)


def list_user_plots(connection: psycopg.Connection, user_id: int) -> list[PlotInfo]:
    """Las parcelas activas de la cuenta (para la lista de la web). Nunca las de otra cuenta."""
    rows = connection.execute(
        f"SELECT {_PLOT_COLUMNS} FROM plot p JOIN app_user u ON u.user_id = p.user_id "
        "WHERE p.user_id = %s AND p.is_active AND u.is_active ORDER BY p.name",
        (user_id,),
    ).fetchall()
    return [_plot_info(row) for row in rows]


def find_user_plot(connection: psycopg.Connection, user_id: int, plot_code: str) -> PlotInfo | None:
    """Busca por `plot_code` **solo entre las parcelas de la cuenta**. El código no da acceso por sí solo."""
    code = plot_code.strip().upper()
    for plot in list_user_plots(connection, user_id):
        if plot.plot_code == code:
            return plot
    return None


# --- Lectura de datos ----------------------------------------------------------------------------

def reference_plot_for(connection: psycopg.Connection, plot_id: int) -> ReferencePlot | None:
    """`REFERENCE_PLOT` si los sensores de la parcela son los de la unidad de referencia (`<unidad>_<profundidad>`)."""
    codes = [
        row[0] for row in connection.execute("SELECT external_code FROM sensor WHERE plot_id = %s AND is_active", (plot_id,)).fetchall()
    ]
    prefix = f"{REFERENCE_PLOT.unit_id}_"
    if codes and all(code and code.startswith(prefix) for code in codes):
        return REFERENCE_PLOT
    return None


def latest_origin(connection: psycopg.Connection, plot_id: int, origin_date: dt.date | None = None) -> dt.date | None:
    """El último origen (día UTC) con predicciones de las sondas de la parcela; `origin_date` lo fija si tiene datos."""
    if origin_date is not None:
        row = connection.execute(
            "SELECT 1 FROM ml_prediction m JOIN sensor s USING (sensor_id) "
            "WHERE s.plot_id = %s AND (m.origin_at AT TIME ZONE 'UTC')::date = %s LIMIT 1",
            (plot_id, origin_date),
        ).fetchone()
        return origin_date if row else None
    row = connection.execute(
        "SELECT max((m.origin_at AT TIME ZONE 'UTC')::date) FROM ml_prediction m JOIN sensor s USING (sensor_id) WHERE s.plot_id = %s",
        (plot_id,),
    ).fetchone()
    return row[0] if row and row[0] else None


def _prediction_rows(connection: psycopg.Connection, plot_id: int, origin: dt.date) -> list[tuple]:
    return connection.execute(
        "SELECT s.depth_cm, m.horizon_days, m.target_date, m.moisture_pred, m.base_moisture, m.method, m.quality, m.limitations "
        "FROM ml_prediction m JOIN sensor s USING (sensor_id) "
        "WHERE s.plot_id = %s AND s.is_active AND (m.origin_at AT TIME ZONE 'UTC')::date = %s "
        "ORDER BY s.depth_cm, m.horizon_days",
        (plot_id, origin),
    ).fetchall()


def _sensor_predictions(rows: list[tuple]) -> tuple[SensorPrediction, ...]:
    return tuple(
        SensorPrediction(
            depth_cm=int(depth), horizon_days=int(horizon),
            moisture_pred=None if pred is None else float(pred), base_moisture=None if base is None else float(base),
            method=method, quality=quality, limitations=tuple(limitations or ()),
        )
        for depth, horizon, _target, pred, base, method, quality, limitations in rows
    )


def _weather_days(connection: psycopg.Connection, plot_id: int, origin: dt.date) -> tuple[WeatherDay, ...]:
    """Una fila por fecha de origen+1 a origen+7. Se prefiere la antelación `fecha − origen`; si falta, la más corta."""
    rows = connection.execute(
        "SELECT fecha, lead_days, eto, rainmm FROM forecast_data WHERE plot_id = %s AND fecha > %s AND fecha <= %s "
        "ORDER BY fecha, lead_days",
        (plot_id, origin, origin + dt.timedelta(days=PLAN_DAYS)),
    ).fetchall()
    chosen: dict[dt.date, tuple] = {}
    for fecha, lead, eto, rain in rows:
        exact = (fecha - origin).days
        current = chosen.get(fecha)
        if current is None or (lead == exact and current[1] != exact):
            chosen[fecha] = (fecha, lead, eto, rain)
    return tuple(
        WeatherDay(date=f, eto_mm=None if eto is None else float(eto), rain_mm=None if rain is None else float(rain), lead_days=int(lead))
        for f, lead, eto, rain in (chosen[k] for k in sorted(chosen))
    )


def load_typical_error(connection: psycopg.Connection) -> tuple[HorizonError, ...]:
    """Error típico por horizonte de los modelos activos (`ml_model.metrics`, contrato C4). Sin métricas: vacío."""
    rows = connection.execute("SELECT horizon_days, metrics FROM ml_model WHERE is_active ORDER BY horizon_days").fetchall()
    return typical_error_from_model_metrics([(int(h), m) for h, m in rows])


# --- Recomendación -------------------------------------------------------------------------------

def _jsonable(model: Any) -> Any:
    return json.loads(json.dumps(model.model_dump(mode="json")))


def _store_recommendation(
    connection: psycopg.Connection, plot_id: int, engine_input: EngineInput, result: EngineResult, now: dt.datetime
) -> tuple[int, int, bool]:
    """Guarda la recomendación (o reutiliza la idéntica). Devuelve `(recommendation_id, version, reutilizada)`."""
    snapshot = {"engine_input": _jsonable(engine_input)}
    last = connection.execute(
        "SELECT recommendation_id, version, inputs_snapshot, method_version, parameter_set_version FROM recommendation "
        "WHERE plot_id = %s AND period_start = %s ORDER BY version DESC LIMIT 1",
        (plot_id, result.period_start),
    ).fetchone()
    if last and last[2] == snapshot and last[3] == result.method_version and last[4] == result.parameter_set_version:
        return int(last[0]), int(last[1]), True
    reasons = {
        "first_day_reasons": list(result.plan[0].reasons), "irrigation_day": result.irrigation_day,
        "irrigation_date": None if result.irrigation_date is None else result.irrigation_date.isoformat(),
        "net_dose_mm": result.net_dose_mm, "gross_dose_mm": result.gross_dose_mm, "volume_m3": result.volume_m3,
        "dose_criterion": result.dose_criterion, "replenishment_interval_days": result.replenishment_interval_days,
        "dose_on_wetted_strip": result.dose_on_wetted_strip, "indicative": result.indicative,
        "factors": [_jsonable(f) for f in result.factors],
        "sensitivity": None if result.sensitivity is None else _jsonable(result.sensitivity),
    }
    version = 1 if last is None else int(last[1]) + 1
    recommendation_id = connection.execute(
        "INSERT INTO recommendation (plot_id, period_start, period_end, version, supersedes_id, calculated_at, decision, "
        "validation_status, daily_plan, reasons, inputs_snapshot, limitations, needs_review, method_version, parameter_set_version) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING recommendation_id",
        (
            plot_id, result.period_start, result.period_end, version, None if last is None else last[0], now, result.decision,
            result.validation_status, Jsonb([_jsonable(d) for d in result.plan]), Jsonb(reasons), Jsonb(snapshot),
            Jsonb([_jsonable(lim) for lim in result.limitations]), result.validation_status == "insufficient_data",
            result.method_version, result.parameter_set_version,
        ),
    ).fetchone()[0]
    return int(recommendation_id), version, False


# --- Informe -------------------------------------------------------------------------------------

def _plot_label(plot: PlotInfo) -> str:
    return f"Parcela {plot.name}" + (f" ({plot.crop})" if plot.crop else "")


def _checks(content: ReportContent) -> dict[str, Any]:
    """Resultado de la validación previa al guardado: cifras del texto frente a las del motor, y motivo de respaldo."""
    text = content.text
    assert text is not None
    check = validate_fields({"summary": text.summary, "reason": text.reason, "warning": text.warning}, content.figures())
    return {
        "figures_ok": check.ok, "problems": list(check.problems), "generator": text.generator,
        "fallback_reason": text.fallback_reason, "indicative": content.indicative,
        "engine_validation_status": content.validation_status,
        "sections": ["summary", "reason", "warning"], "typical_error_horizons": [e.horizon_days for e in content.typical_error],
    }


def _stored(row: tuple, content: ReportContent, today: dt.date | None, created: bool, version: int, reused: bool) -> StoredReport:
    report_id, recommendation_id, report_type, generated_at, generator, generator_version, checks = row
    return StoredReport(
        report_id=int(report_id), recommendation_id=int(recommendation_id), report_type=report_type, generated_at=generated_at,
        content=content.model_copy(update={"today": today}), generator=generator, generator_version=generator_version,
        validation_checks=checks or {}, created=created, recommendation_version=version, recommendation_reused=reused,
    )


def _today(plot: PlotInfo, today: dt.date | None, now: dt.datetime) -> dt.date:
    return today if today is not None else now.astimezone(ZoneInfo(plot.timezone)).date()


def generate_report(
    plot_id: int,
    user_id: int,
    report_type: str,
    *,
    connection: psycopg.Connection | None = None,
    origin_date: dt.date | None = None,
    today: dt.date | None = None,
    text_generator: TextGenerator | None = None,
    now: dt.datetime | None = None,
) -> ReportOutcome:
    """Genera (o devuelve, si ya existe) el informe `daily` o `weekly` de una parcela del usuario.

    `connection`: la de la web; sin ella se abre una con `db.load_config()` y se cierra al terminar.
    `origin_date`: fija el origen (para repetir un día); por defecto, el último con predicciones.
    `today`: día de la consulta en la zona de la parcela (por defecto, hoy). `text_generator`: el LLM; sin él, plantilla.
    """
    if report_type not in REPORT_TYPES:
        raise ReportError("invalid_report_type")
    owns = connection is None
    try:
        connection = connection or db.connect(db.load_config())
        return _generate(connection, plot_id, user_id, report_type, origin_date, today, text_generator, now or dt.datetime.now(dt.timezone.utc))
    except ReportError:
        raise
    except psycopg.Error:
        logger.exception("Error de base de datos al generar el informe")
        raise ReportError("database_error") from None
    finally:
        if owns and connection is not None:
            connection.close()


def _generate(
    connection: psycopg.Connection, plot_id: int, user_id: int, report_type: str, origin_date: dt.date | None,
    today: dt.date | None, text_generator: TextGenerator | None, now: dt.datetime,
) -> ReportOutcome:
    plot = get_user_plot(connection, plot_id, user_id)  # 1. permiso, antes de leer nada más
    origin = latest_origin(connection, plot.plot_id, origin_date)
    if origin is None:
        return ReportOutcome("no_prediction", plot, NO_PREDICTION_MESSAGE)
    predictions = _prediction_rows(connection, plot.plot_id, origin)
    reference = reference_plot_for(connection, plot.plot_id)
    if reference is None:
        forecast = tuple(
            {"depth_cm": int(d), "horizon_days": int(h), "target_date": t, "moisture_pred": None if p is None else float(p), "quality": q}
            for d, h, t, p, _b, _m, q, _l in predictions
        )
        return ReportOutcome("crop_data_unavailable", plot, CROP_DATA_MESSAGE, origin, None, forecast)

    try:
        engine_input = EngineInput(
            plot=reference, origin_date=origin, predictions=_sensor_predictions(predictions),
            weather=_weather_days(connection, plot.plot_id, origin), replenishment_interval_days=plot.irrigation_interval_days,
        )
        result = run_engine(engine_input)
    except (ValidationError, ValueError):
        logger.exception("El motor rechazó las entradas de la parcela %s", plot.plot_id)
        raise ReportError("engine_error") from None

    with connection.transaction():
        recommendation_id, version, reused = _store_recommendation(connection, plot.plot_id, engine_input, result, now)
        content = build_report_content(
            result, report_type=report_type, plot_label=_plot_label(plot), typical_error=load_typical_error(connection),
        )
        text = generate_text(content, text_generator)
        content = content.with_text(text)
        checks = _checks(content)
        text_es = "\n\n".join((text.summary, text.reason, text.warning))
        stored_content = Jsonb(content.model_dump(mode="json", exclude={"today"}))
        inserted = connection.execute(
            "INSERT INTO report (recommendation_id, report_type, generated_at, content, text_es, generator, generator_version, validation_checks) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (recommendation_id, report_type, generator, generator_version) DO NOTHING "
            "RETURNING report_id, recommendation_id, report_type, generated_at, generator, generator_version, validation_checks",
            (recommendation_id, report_type, now, stored_content, text_es, text.generator, text.generator_version, Jsonb(checks)),
        ).fetchone()
        created = inserted is not None
        if inserted is None:  # ya existía el mismo informe: se devuelve el guardado, sin recalcular su texto
            inserted = connection.execute(
                "SELECT report_id, recommendation_id, report_type, generated_at, generator, generator_version, validation_checks "
                "FROM report WHERE recommendation_id = %s AND report_type = %s AND generator = %s AND generator_version = %s",
                (recommendation_id, report_type, text.generator, text.generator_version),
            ).fetchone()
            stored_json = connection.execute("SELECT content FROM report WHERE report_id = %s", (inserted[0],)).fetchone()[0]
            content = ReportContent.model_validate(stored_json)
    connection.commit()
    report = _stored(inserted, content, _today(plot, today, now), created, version, reused)
    return ReportOutcome("generated", plot, "", origin, report)


# --- Lectura de un informe guardado y PDF --------------------------------------------------------

def get_report(connection: psycopg.Connection, report_id: int, user_id: int, *, today: dt.date | None = None, now: dt.datetime | None = None) -> StoredReport:
    """Un informe ya guardado, solo si su parcela es del usuario. No recalcula nada."""
    row = connection.execute(
        "SELECT r.report_id, r.recommendation_id, r.report_type, r.generated_at, r.generator, r.generator_version, r.validation_checks, "
        "r.content, rec.version, p.plot_id, p.timezone "
        "FROM report r JOIN recommendation rec USING (recommendation_id) JOIN plot p ON p.plot_id = rec.plot_id "
        "JOIN app_user u ON u.user_id = p.user_id WHERE r.report_id = %s AND p.user_id = %s AND p.is_active AND u.is_active",
        (report_id, user_id),
    ).fetchone()
    if row is None:
        raise AccessDenied()
    content = ReportContent.model_validate(row[7])
    day = today if today is not None else (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(row[10])).date()
    return _stored(row[:7], content, day, created=False, version=int(row[8]), reused=True)


def build_report_pdf(
    connection: psycopg.Connection, report_id: int, user_id: int, *, today: dt.date | None = None,
    template_dir: Path | str | None = None,
) -> bytes:
    """El PDF de un informe guardado (se genera al pedirlo; no se guarda). Comprueba el permiso como `get_report`."""
    from irrigation_app.reports import pdf as pdf_module
    from irrigation_app.reports.render import make_charts, render_report_html

    report = get_report(connection, report_id, user_id, today=today)
    snapshot = connection.execute("SELECT inputs_snapshot, method_version FROM recommendation WHERE recommendation_id = %s", (report.recommendation_id,)).fetchone()
    images: dict[str, str | None] = {}
    try:
        engine_input = EngineInput.model_validate(snapshot[0]["engine_input"])
        result = run_engine(engine_input)  # determinista: mismas entradas, mismo resultado
        if result.method_version == snapshot[1]:
            images = make_charts(result, engine_input, report.content)
    except (ValidationError, ValueError, KeyError):
        logger.warning("No se pudieron rehacer las gráficas del informe %s", report_id)
    rendered = render_report_html(report.content, images, template_dir=template_dir)
    import tempfile

    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = pdf_module.print_pdf(rendered.html, Path(tmp) / "informe.pdf")
            return path.read_bytes()
    except (pdf_module.PdfUnavailableError, pdf_module.PdfGenerationError):
        raise ReportError("pdf_unavailable") from None
