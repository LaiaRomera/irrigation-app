"""Prepara en el PostgreSQL LOCAL los datos de un día de origen de la parcela de referencia y genera sus informes.

Sirve para probar el servicio de informes (7.4) con datos reales de la 1168 sin esperar a la tarea diaria (fase 8):
1. predice la humedad con los modelos activos (`models.inference.predict_plot`, de DataEngineerML) y la guarda en
   `ml_prediction` (idempotente: un origen ya guardado no se toca);
2. copia a `forecast_data` la previsión archivada que habría ese día (Previous Runs, antelaciones 0 a 6; no hay
   antelación 7, así que el día 7 del plan queda sin meteorología);
3. llama a `generate_report` (diario y semanal) con la plantilla de respaldo y, si se pide, escribe el PDF.

Uso (desde la raíz del proyecto, siempre contra el Docker local):
    .venv/Scripts/python tests/fixtures/seed_reference_origin.py --origin 2026-08-25 [--pdf-dir data/reports_examples]

Rechaza cualquier servidor que no sea local. Los datos de la empresa se leen de `data/` (fuera de git).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from irrigation_app import db  # noqa: E402
from irrigation_app.data_layers import DATA_DIR  # noqa: E402
from irrigation_app.models import inference, registry  # noqa: E402
from irrigation_app.reference_plot import REFERENCE_PLOT  # noqa: E402
from irrigation_app.reports import service  # noqa: E402

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
PREVIOUS_RUNS = DATA_DIR / "silver" / "weather_previous_runs.parquet"
UNIT_LOCATIONS = DATA_DIR / "silver" / "weather_unit_locations.parquet"


def reference_plot_id(connection) -> tuple[int, int]:
    row = connection.execute(
        "SELECT p.plot_id, p.user_id FROM plot p JOIN sensor s USING (plot_id) WHERE s.external_code = %s", (f"{REFERENCE_PLOT.unit_id}_15",)
    ).fetchone()
    if row is None:
        sys.exit("La parcela de referencia no está en la base de datos local (scripts/load_reference_plot.py).")
    return int(row[0]), int(row[1])


def seed_origin(connection, plot_id: int, origin: dt.date) -> dict[str, int]:
    """Guarda `forecast_data` y `ml_prediction` de ese origen. Devuelve cuántas filas nuevas entraron."""
    locations = pd.read_parquet(UNIT_LOCATIONS)
    location = locations.loc[locations["unit_id"] == REFERENCE_PLOT.unit_id, "location_id"].iloc[0]
    runs = pd.read_parquet(PREVIOUS_RUNS, filters=[("location_id", "==", location)])
    weather = inference.weather_for_origin(runs, location, origin)
    captured = dt.datetime(origin.year, origin.month, origin.day, 5, tzinfo=dt.timezone.utc)
    new_weather = 0
    with connection.transaction():
        for row in weather.itertuples():
            cursor = connection.execute(
                "INSERT INTO forecast_data (plot_id, fecha, captured_at, lead_days, source, tmedia, rhmedia, vientomedia, eto, rainmm) "
                "VALUES (%s, %s, %s, %s, 'previous_runs', %s, %s, %s, %s, %s) ON CONFLICT (plot_id, fecha, lead_days, source) DO NOTHING",
                (plot_id, row.day.date(), captured, int(row.lead_days), float(row.temperature_c), float(row.humidity_pct),
                 float(row.wind_kmh), float(row.et0_mm), float(row.precipitation_mm)),
            )
            new_weather += cursor.rowcount
    before = connection.execute("SELECT count(*) FROM ml_prediction").fetchone()[0]
    inference.predict_plot(connection, plot_id, origin, weather, registry.load_active_models(connection))
    connection.commit()
    return {"forecast_rows": new_weather, "prediction_rows": connection.execute("SELECT count(*) FROM ml_prediction").fetchone()[0] - before}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", default=".env.docker-pg")
    parser.add_argument("--origin", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--pdf-dir", type=Path, help="si se pasa, escribe aquí los PDF diario y semanal")
    args = parser.parse_args()

    config = db.load_config(args.env_file)
    if config.host not in LOCAL_HOSTS:
        sys.exit("Este script solo escribe en el PostgreSQL local.")
    with db.connect(config) as connection:
        plot_id, user_id = reference_plot_id(connection)
        print("Datos nuevos:", seed_origin(connection, plot_id, args.origin))
        for report_type in ("daily", "weekly"):
            outcome = service.generate_report(plot_id, user_id, report_type, connection=connection, origin_date=args.origin, today=args.origin)
            report = outcome.report
            c = report.content
            print(f"{report_type}: {outcome.status}, informe {report.report_id} (recomendación {report.recommendation_id} v{report.recommendation_version}"
                  f"{', reutilizada' if report.recommendation_reused else ''}), {c.decision}, {c.validation_status}, texto {report.generator}"
                  f"{'' if report.created else ' (ya existía)'}, primer día {c.first_day.date}: {c.first_day.decision_label}, "
                  f"dosis neta {c.first_day.net_dose_mm}, limitaciones {[l.code for l in c.limitations]}")
            if args.pdf_dir:
                args.pdf_dir.mkdir(parents=True, exist_ok=True)
                target = args.pdf_dir / f"real_1168_{args.origin}_{report_type}.pdf"
                target.write_bytes(service.build_report_pdf(connection, report.report_id, user_id, today=args.origin))
                print("  PDF:", target.name, target.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
