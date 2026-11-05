"""Descarga las previsiones archivadas de Open-Meteo de todas las ubicaciones de las unidades.

Uso (desde la raíz del proyecto, después de prepare_agrodevices_units.py y prepare_daily_readings.py):
    .venv/Scripts/python scripts/download_weather.py

Pide, para cada ubicación (unidades a menos de 1 km agrupadas), las antelaciones de 0 a 6 días
entre el primer y el último día con lecturas de sus unidades (unas 6.500 llamadas de las 10.000
diarias de la capa gratuita; el límite por hora es de 5.000, así que puede parar y pedir que se
vuelva a ejecutar más tarde). Cada ubicación se guarda tal cual en su propio archivo de bronce,
data/bronze/weather/previous_runs/, así que si se corta se puede volver a ejecutar y solo pide las
que faltan. Al final reúne todo en data/silver/weather_previous_runs.parquet y escribe las tablas
data/silver/weather_locations.parquet y data/silver/weather_unit_locations.parquet (data/ no se
sube a git).
"""

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from irrigation_app.data_layers import (  # noqa: E402
    BRONZE_WEATHER_DIR,
    DAILY_PATH,
    UNITS_PATH,
    WEATHER_LOCATIONS_PATH,
    WEATHER_PREVIOUS_RUNS_PATH,
    WEATHER_UNIT_LOCATIONS_PATH,
)
from irrigation_app.weather.locations import locations_from_units  # noqa: E402
from irrigation_app.weather.open_meteo import RateLimitedError, WeatherError, fetch_previous_runs  # noqa: E402

PAUSE_SECONDS = 1.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--units", type=Path, default=UNITS_PATH)
    parser.add_argument("--daily", type=Path, default=DAILY_PATH)
    parser.add_argument("--bronze-dir", type=Path, default=BRONZE_WEATHER_DIR, help="un Parquet por ubicación, tal como llega")
    parser.add_argument("--output-previous-runs", type=Path, default=WEATHER_PREVIOUS_RUNS_PATH)
    parser.add_argument("--output-locations", type=Path, default=WEATHER_LOCATIONS_PATH)
    parser.add_argument("--output-unit-locations", type=Path, default=WEATHER_UNIT_LOCATIONS_PATH)
    parser.add_argument("--pause", type=float, default=PAUSE_SECONDS, help="segundos entre peticiones")
    args = parser.parse_args()

    for path in (args.units, args.daily):
        if not path.exists():
            sys.exit(f"No existe el archivo {path}")

    units = pd.read_parquet(args.units)
    locations, unit_locations = locations_from_units(units)

    # Cada ubicación solo pide los días en que alguna de sus unidades tiene lecturas: Open-Meteo
    # cuenta cada petición según los días y variables que pide (gratis: 10.000 llamadas al día y
    # 5.000 por hora), y pedir todo el periodo para todas supera el límite diario.
    unit_days = pd.read_parquet(args.daily, columns=["unit_id", "day"]).groupby("unit_id")["day"].agg(["min", "max"])
    periods = unit_days.join(unit_locations.set_index("unit_id")).groupby("location_id").agg(start=("min", "min"), end=("max", "max"))
    cache_dir = args.bronze_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    for path in (args.output_previous_runs, args.output_locations, args.output_unit_locations):
        path.parent.mkdir(parents=True, exist_ok=True)
    locations.to_parquet(args.output_locations, index=False)
    unit_locations.to_parquet(args.output_unit_locations, index=False)

    print(f"{len(units)} unidades en {len(locations)} ubicaciones")
    failed = []
    for number, location in enumerate(locations.itertuples(index=False), start=1):
        target = cache_dir / f"{location.location_id}.parquet"
        if target.exists():
            print(f"[{number}/{len(locations)}] {location.location_id}: ya descargada")
            continue
        try:
            period = periods.loc[location.location_id]
            forecasts = fetch_previous_runs(location.latitude, location.longitude, period.start.date(), period.end.date())
        except RateLimitedError as error:
            print(f"[{number}/{len(locations)}] {location.location_id}: límite de peticiones ({error}). Se para; vuelve a ejecutar más tarde.")
            break
        except WeatherError as error:
            print(f"[{number}/{len(locations)}] {location.location_id}: FALLÓ ({error})")
            failed.append(location.location_id)
            continue
        forecasts.assign(location_id=location.location_id).to_parquet(target, index=False)
        print(f"[{number}/{len(locations)}] {location.location_id}: {len(forecasts):,} filas")
        time.sleep(args.pause)

    files = sorted(cache_dir.glob("*.parquet"))
    if files:
        combined = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        combined.to_parquet(args.output_previous_runs, index=False)
        print(f"\n{len(files)} de {len(locations)} ubicaciones descargadas, {len(combined):,} filas en total")
    if failed:
        print("Fallaron:", ", ".join(failed), "(vuelve a ejecutar para reintentarlas)")


if __name__ == "__main__":
    main()
