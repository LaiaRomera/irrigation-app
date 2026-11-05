"""Describe las capas de datos (bronce, plata y oro) y guarda un manifiesto por capa.

Uso (desde la raíz del proyecto):
    .venv/Scripts/python scripts/describe_data_layers.py [--bronze-dir CARPETA_DE_EXPORTS]

- Plata y oro: lee los Parquet reales de data/silver/ y data/gold/ y cuenta filas, columnas,
  unidades, rango de fechas y tamaño en disco.
- Bronce (meteorología): los Parquet tal como los devolvió Open-Meteo, uno por ubicación, en
  data/bronze/weather/previous_runs/. Se anotan nº de archivos, filas, ubicaciones y rango de días.
- Bronce (exports): los exports originales de Agrodevices están fuera del repositorio. Se indican con
  --bronze-dir o con la variable de entorno AGRODEVICES_EXPORTS_DIR. Por cada CSV se anotan nombre,
  tamaño, filas, rango de fechas (si tiene columna `fecha`) y un hash sha256 corto. Si la carpeta no
  existe, el script lo dice y sigue sin fallar.

Los manifiestos (data/<capa>/manifest.json) no contienen datos, solo recuentos. data/ no se sube a git.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from irrigation_app.data_layers import (  # noqa: E402
    BRONZE_EXPORTS_ENV,
    BRONZE_MANIFEST_PATH,
    BRONZE_WEATHER_DIR,
    GOLD_FILES,
    GOLD_MANIFEST_PATH,
    SILVER_FILES,
    SILVER_MANIFEST_PATH,
    bronze_exports_dir,
)

# Columna de fecha de cada archivo de plata u oro (los que no tienen, no dan rango).
DATE_COLUMNS = {
    "readings.parquet": "timestamp",
    "daily.parquet": "day",
    "snapshots.parquet": "day",
    "training_table.parquet": "origin_day",
    "weather_previous_runs.parquet": "day",
}
UNIT_COLUMN = "unit_id"
BRONZE_DATE_COLUMN = "fecha"
HASH_LENGTH = 12
CHUNK_ROWS = 1_000_000


def _date_range(series: pd.Series) -> tuple[str, str]:
    return str(series.min().date()), str(series.max().date())


def describe_parquet(path: Path) -> dict:
    frame = pd.read_parquet(path)
    info = {
        "file": path.name,
        "size_bytes": path.stat().st_size,
        "rows": len(frame),
        "columns": list(frame.columns),
    }
    if UNIT_COLUMN in frame.columns:
        info["units"] = int(frame[UNIT_COLUMN].nunique())
    date_column = DATE_COLUMNS.get(path.name)
    if date_column is not None:
        info["date_column"] = date_column
        info["date_from"], info["date_to"] = _date_range(frame[date_column])
    return info


def describe_sample_csv(path: Path) -> dict:
    frame = pd.read_csv(path, sep=None, engine="python")
    return {"file": path.name, "size_bytes": path.stat().st_size, "rows": len(frame), "columns": list(frame.columns)}


def describe_silver_gold_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    return describe_parquet(path) if path.suffix == ".parquet" else describe_sample_csv(path)


def short_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:HASH_LENGTH]


def describe_export(path: Path) -> dict:
    """Recuento de un export original: nada de su contenido se guarda, solo cifras."""
    header = pd.read_csv(path, nrows=0).columns
    info = {"file": path.name, "size_bytes": path.stat().st_size, "sha256_short": short_sha256(path)}
    rows, first, last = 0, None, None
    columns = [BRONZE_DATE_COLUMN] if BRONZE_DATE_COLUMN in header else [header[0]]
    for chunk in pd.read_csv(path, usecols=columns, dtype=str, chunksize=CHUNK_ROWS):
        rows += len(chunk)
        if BRONZE_DATE_COLUMN in header:
            values = chunk[BRONZE_DATE_COLUMN].dropna()
            if not values.empty:
                first = values.min() if first is None else min(first, values.min())
                last = values.max() if last is None else max(last, values.max())
    info["rows"] = rows
    info["n_columns"] = len(header)
    if first is not None:
        info["date_from"], info["date_to"] = first[:10], last[:10]
    return info


def describe_bronze(directory: Path | None) -> dict:
    if directory is None or not directory.is_dir():
        return {"available": False, "files": []}
    return {"available": True, "files": [describe_export(p) for p in sorted(directory.glob("*.csv"))]}


def describe_bronze_weather(directory: Path) -> dict:
    """Recuento de la meteorología descargada tal cual: un Parquet por ubicación, solo cifras."""
    files = sorted(directory.glob("*.parquet")) if directory.is_dir() else []
    if not files:
        return {"available": False}
    rows, first, last = 0, None, None
    for path in files:
        frame = pd.read_parquet(path, columns=["day", "lead_days"])
        rows += len(frame)
        low, high = frame["day"].min(), frame["day"].max()
        first = low if first is None else min(first, low)
        last = high if last is None else max(last, high)
    return {
        "available": True,
        "directory": str(directory.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "locations": len(files),
        "size_bytes": sum(p.stat().st_size for p in files),
        "rows": rows,
        "date_from": str(first.date()),
        "date_to": str(last.date()),
    }


def write_manifest(path: Path, layer: str, files: list[dict], extra: dict | None = None) -> None:
    manifest = {"layer": layer, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "files": files}
    manifest.update(extra or {})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def format_file(info: dict) -> str:
    parts = [f"{info['size_bytes'] / 1e6:,.1f} MB", f"{info['rows']:,} filas"]
    if "columns" in info:
        parts.append(f"{len(info['columns'])} columnas")
    if "n_columns" in info:
        parts.append(f"{info['n_columns']} columnas")
    if "units" in info:
        parts.append(f"{info['units']} unidades")
    if "date_from" in info:
        parts.append(f"{info['date_from']} a {info['date_to']}")
    if "sha256_short" in info:
        parts.append(f"sha256 {info['sha256_short']}")
    return f"  {info['file']:<34}" + ", ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bronze-dir", type=Path, default=bronze_exports_dir(), help=f"carpeta de los exports (o variable {BRONZE_EXPORTS_ENV})")
    args = parser.parse_args()

    print("BRONCE (exports originales de Agrodevices, fuera del repositorio, y meteorología tal cual)")
    bronze = describe_bronze(args.bronze_dir)
    if bronze["available"]:
        for info in bronze["files"]:
            print(format_file(info))
        export_files = bronze["files"]
    else:
        print(f"  No se encuentra la carpeta de exports (usa --bronze-dir o la variable {BRONZE_EXPORTS_ENV}); se conserva lo anotado antes.")
        previous = json.loads(BRONZE_MANIFEST_PATH.read_text(encoding="utf-8")) if BRONZE_MANIFEST_PATH.exists() else {}
        export_files = previous.get("files", [])
    weather = describe_bronze_weather(BRONZE_WEATHER_DIR)
    if weather["available"]:
        print(
            f"  weather/previous_runs/ {weather['locations']} ubicaciones, {weather['size_bytes'] / 1e6:,.1f} MB, "
            f"{weather['rows']:,} filas, {weather['date_from']} a {weather['date_to']}"
        )
    if export_files or weather["available"]:
        write_manifest(BRONZE_MANIFEST_PATH, "bronze", export_files, {"weather": weather})
        print(f"  Manifiesto: {BRONZE_MANIFEST_PATH.relative_to(PROJECT_ROOT)}")

    for layer, name, paths, manifest_path in (
        ("silver", "PLATA (limpios y tipados)", SILVER_FILES, SILVER_MANIFEST_PATH),
        ("gold", "ORO (lo que consumen el modelo y la app)", GOLD_FILES, GOLD_MANIFEST_PATH),
    ):
        print(f"\n{name}")
        files = [info for info in (describe_silver_gold_file(p) for p in paths) if info is not None]
        for p in paths:
            if not p.exists():
                print(f"  {p.name:<34}no existe todavía")
        for info in files:
            print(format_file(info))
        if files:
            write_manifest(manifest_path, layer, files)
            print(f"  Manifiesto: {manifest_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
