"""Carga la parcela de referencia (unidad 1168) en la base de datos (tarea 5.4).

Uso (desde la raíz del proyecto):
    .venv/Scripts/python scripts/load_reference_plot.py --env-file .env.docker-pg            # PostgreSQL local
    .venv/Scripts/python scripts/load_reference_plot.py --env-file .env.docker-pg --dry-run  # solo cuenta, no escribe
    .venv/Scripts/python scripts/load_reference_plot.py --env-file .env --allow-remote       # Aiven (ver abajo)

Qué carga, con los datos de `REFERENCE_PLOT` (`reference_plot.py`) y de la capa plata (`data/silver/`):
- un usuario de prueba en `app_user` (el email se puede cambiar con `--user-email`);
- la parcela en `plot` (nombre, coordenadas, cultivo y textura de `REFERENCE_PLOT`) y su `plot_code`, un código aleatorio
  de 8 caracteres que se genera al crearla, no se cambia después y se muestra por consola (no se guarda en archivos);
- un sensor por profundidad en `sensor` (`external_code` = `<unidad>_<profundidad>`);
- las lecturas de 15 minutos en `sensor_reading`, sin resumir: `value` es el índice de 0 a 100,
  `is_interpolated` es verdadero para el tipo `SMMi`, y `quality_flag` es `ok`. **Excepción:** las lecturas
  de tipo `SMMAdmin` (días de instalación, con la sonda sin enterrar) entran con `quality_flag = 'admin'`,
  para que la predicción no las use como si fueran suelo.

Es idempotente: se puede ejecutar todas las veces que haga falta; el usuario, la parcela, los sensores
y las lecturas que ya existen no se duplican (las lecturas, por la unicidad `sensor_id` + `measured_at`).

Seguridad: sin `--allow-remote` solo escribe en un servidor local (`localhost` o `127.0.0.1`), para no
llenar por descuido el gigabyte gratuito de Aiven (la carga son unas 226.000 filas). Nunca imprime la
contraseña, y los datos de la empresa se leen de `data/` (fuera de git).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import psycopg

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from irrigation_app import db  # noqa: E402
from irrigation_app.data_layers import READINGS_PATH, UNITS_PATH  # noqa: E402
from irrigation_app.plot_code import generate_plot_code  # noqa: E402
from irrigation_app.reference_plot import REFERENCE_PLOT, ReferencePlot  # noqa: E402
from irrigation_app.scale import moisture_index  # noqa: E402

DEFAULT_USER_EMAIL = "usuario.prueba@example.com"
DEFAULT_USER_NAME = "Usuario de prueba"
DEFAULT_TIMEZONE = "Europe/Madrid"  # la 1168 está en Lleida (41,99 N, 0,21 E); el día de la parcela es el de España
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
READING_FIELDS = ["sensor_id", "measured_at", "value", "is_interpolated", "quality_flag", "quality_reason"]
ADMIN_REASON = "Lectura administrativa (SMMAdmin) de un día de instalación"


def build_reading_rows(readings: pd.DataFrame, unit: pd.Series, sensor_depths: tuple[int, ...]) -> pd.DataFrame:
    """Lecturas de la unidad listas para `sensor_reading` (todavía con `depth_cm` en lugar de `sensor_id`).

    `readings` es la capa plata (`unit_id`, `timestamp`, `depth_cm`, `frequency`, `reading_type`) y `unit` la
    fila de la unidad con su rango de la escala (`smm_min`, `smm_max`). El índice se redondea a 4 decimales,
    como la columna. Una profundidad que no está en `sensor_depths` es un error, no se descarta en silencio.
    """
    own = readings[readings["unit_id"] == unit["unit_id"]]
    unknown = sorted(set(own["depth_cm"].astype(int)) - set(sensor_depths))
    if unknown:
        raise ValueError(f"La unidad {unit['unit_id']} tiene lecturas a profundidades sin sensor: {unknown}")
    frequency = own["frequency"].astype(float)
    smm_min = pd.Series(float(unit["smm_min"]), index=own.index)
    smm_max = pd.Series(float(unit["smm_max"]), index=own.index)
    is_admin = own["reading_type"] == "SMMAdmin"
    rows = pd.DataFrame(
        {
            "depth_cm": own["depth_cm"].astype(int),
            "measured_at": pd.to_datetime(own["timestamp"], utc=True),
            "value": moisture_index(frequency, smm_min, smm_max).round(4),
            "is_interpolated": own["reading_type"] == "SMMi",
            "quality_flag": is_admin.map({True: "admin", False: "ok"}),
            "quality_reason": is_admin.map({True: ADMIN_REASON, False: None}),
        }
    )
    return rows.dropna(subset=["value"]).reset_index(drop=True)


def ensure_user(connection: psycopg.Connection, email: str, name: str) -> int:
    connection.execute(
        "INSERT INTO app_user (email, name) VALUES (%s, %s) ON CONFLICT (email) DO NOTHING", (email.lower(), name)
    )
    return int(connection.execute("SELECT user_id FROM app_user WHERE email = %s", (email.lower(),)).fetchone()[0])


def ensure_plot(connection: psycopg.Connection, user_id: int, plot: ReferencePlot, unit: pd.Series) -> tuple[int, str]:
    """Busca la parcela por usuario y nombre y la crea si no existe. Devuelve `(plot_id, plot_code)`.

    El código (`plot_code`) se genera al crearla; si la parcela ya existe, no se cambia (solo se le da uno
    si por ser anterior a la migración 002 no lo tenía). Si el código generado choca con otro, se reintenta.
    """
    row = connection.execute("SELECT plot_id, plot_code FROM plot WHERE user_id = %s AND name = %s", (user_id, plot.name)).fetchone()
    if row and row[1]:
        return int(row[0]), row[1]
    for _ in range(10):
        code = generate_plot_code()
        try:
            with connection.transaction():
                if row:
                    connection.execute("UPDATE plot SET plot_code = %s WHERE plot_id = %s", (code, row[0]))
                    return int(row[0]), code
                plot_id = connection.execute(
                    "INSERT INTO plot (user_id, name, latitude, longitude, timezone, crop, soil_texture, plot_code) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING plot_id",
                    (
                        user_id, plot.name, round(float(unit["latitude"]), 6), round(float(unit["longitude"]), 6), DEFAULT_TIMEZONE,
                        plot.crop.value if plot.crop else None, plot.soil_texture.value if plot.soil_texture else None, code,
                    ),
                ).fetchone()[0]
                return int(plot_id), code
        except psycopg.errors.UniqueViolation:
            continue
    raise RuntimeError("No se pudo generar un código de parcela único")


def ensure_sensors(connection: psycopg.Connection, plot_id: int, plot: ReferencePlot) -> dict[int, int]:
    """Un sensor por profundidad; devuelve `{profundidad: sensor_id}`."""
    sensors = {}
    for depth in plot.sensor_depths_cm:
        code = f"{plot.unit_id}_{depth}"
        connection.execute(
            "INSERT INTO sensor (plot_id, depth_cm, external_code) VALUES (%s, %s, %s) ON CONFLICT (external_code) DO NOTHING",
            (plot_id, depth, code),
        )
        row = connection.execute("SELECT sensor_id, plot_id FROM sensor WHERE external_code = %s", (code,)).fetchone()
        if int(row[1]) != plot_id:
            raise RuntimeError(f"El sensor {code} ya existe en otra parcela")
        sensors[depth] = int(row[0])
    return sensors


def insert_readings(connection: psycopg.Connection, rows: pd.DataFrame, sensors: dict[int, int]) -> int:
    """Inserta las lecturas que faltan (COPY a una tabla temporal y `ON CONFLICT DO NOTHING`). Devuelve las nuevas."""
    connection.execute(
        "CREATE TEMP TABLE tmp_reading (sensor_id BIGINT, measured_at TIMESTAMPTZ, value NUMERIC(12, 4), "
        "is_interpolated BOOLEAN, quality_flag VARCHAR(30), quality_reason VARCHAR(255)) ON COMMIT DROP"
    )
    sensor_ids = rows["depth_cm"].map(sensors)
    with connection.cursor() as cursor:
        with cursor.copy(f"COPY tmp_reading ({', '.join(READING_FIELDS)}) FROM STDIN") as copy:
            for sensor_id, ts, value, interpolated, flag, reason in zip(
                sensor_ids, rows["measured_at"], rows["value"], rows["is_interpolated"], rows["quality_flag"], rows["quality_reason"]
            ):
                copy.write_row((int(sensor_id), ts.to_pydatetime(), float(value), bool(interpolated), flag, None if pd.isna(reason) else reason))
        cursor.execute(
            f"INSERT INTO sensor_reading ({', '.join(READING_FIELDS)}) SELECT {', '.join(READING_FIELDS)} FROM tmp_reading "
            "ON CONFLICT (sensor_id, measured_at) DO NOTHING"
        )
        return cursor.rowcount


def load_reference_plot(
    connection: psycopg.Connection,
    plot: ReferencePlot,
    unit: pd.Series,
    rows: pd.DataFrame,
    *,
    user_email: str = DEFAULT_USER_EMAIL,
    user_name: str = DEFAULT_USER_NAME,
) -> dict[str, int | str]:
    """Carga usuario, parcela, sensores y lecturas en una sola transacción. Devuelve los identificadores y el recuento."""
    with connection.transaction():
        user_id = ensure_user(connection, user_email, user_name)
        plot_id, plot_code = ensure_plot(connection, user_id, plot, unit)
        sensors = ensure_sensors(connection, plot_id, plot)
        inserted = insert_readings(connection, rows, sensors)
    return {"user_id": user_id, "plot_id": plot_id, "plot_code": plot_code, "sensors": len(sensors), "readings_total": len(rows), "readings_inserted": inserted}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", default=".env", help="archivo con las variables DB_* (por defecto .env)")
    parser.add_argument("--readings", type=Path, default=READINGS_PATH)
    parser.add_argument("--units", type=Path, default=UNITS_PATH)
    parser.add_argument("--user-email", default=DEFAULT_USER_EMAIL)
    parser.add_argument("--user-name", default=DEFAULT_USER_NAME)
    parser.add_argument("--dry-run", action="store_true", help="solo lee y cuenta; no se conecta ni escribe")
    parser.add_argument("--allow-remote", action="store_true", help="permite escribir en un servidor que no sea local (Aiven)")
    args = parser.parse_args()

    for path in (args.readings, args.units):
        if not path.exists():
            sys.exit(f"No existe el archivo {path}")
    plot = REFERENCE_PLOT
    units = pd.read_parquet(args.units)
    unit = units[units["unit_id"] == plot.unit_id]
    if unit.empty:
        sys.exit(f"La unidad {plot.unit_id} no está en {args.units.name}")
    readings = pd.read_parquet(args.readings, filters=[("unit_id", "==", plot.unit_id)])
    rows = build_reading_rows(readings, unit.iloc[0], plot.sensor_depths_cm)
    print(f"Unidad {plot.unit_id} ({plot.name}): {len(rows):,} lecturas de {rows['depth_cm'].nunique()} profundidades.")
    print(f"  interpoladas: {int(rows['is_interpolated'].sum()):,}; administrativas (quality_flag admin): {int((rows['quality_flag'] == 'admin').sum()):,}")
    if args.dry_run:
        print("Prueba en seco: no se escribe nada.")
        return

    try:
        config = db.load_config(args.env_file)
    except db.DatabaseConfigError as error:
        sys.exit(str(error))
    print(f"Destino: {db.describe(config)}")
    if config.host not in LOCAL_HOSTS and not args.allow_remote:
        sys.exit("El destino no es local. Para escribir en Aiven hay que pasar --allow-remote (decisión de Laia).")
    try:
        connection = db.connect(config)
    except psycopg.OperationalError as error:
        sys.exit(f"No se pudo conectar: {error}")
    with connection:
        result = load_reference_plot(connection, plot, unit.iloc[0], rows, user_email=args.user_email, user_name=args.user_name)
    print(
        f"Hecho: usuario {result['user_id']}, parcela {result['plot_id']} (código {result['plot_code']}), {result['sensors']} sensores. "
        f"Lecturas nuevas: {result['readings_inserted']:,} de {result['readings_total']:,}."
    )


if __name__ == "__main__":
    main()
