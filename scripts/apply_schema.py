"""Crea la base de datos del proyecto en PostgreSQL y aplica sql/create_schema.sql, o aplica una migración.

Uso (desde la raíz del proyecto):
    .venv/Scripts/python scripts/apply_schema.py                  # usa .env (Aiven)
    .venv/Scripts/python scripts/apply_schema.py .env.docker-pg   # PostgreSQL local
    .venv/Scripts/python scripts/apply_schema.py .env.docker-pg --migration sql/migrations/001_ml_tables.sql

Medidas de seguridad:
  * Solo actúa sobre la base `irrigation_app`: se niega si DB_NAME es otra.
  * Sin `--migration`: si la base ya tiene tablas, no hace nada (no pisa datos).
  * Con `--migration`: la base debe existir y tener ya el esquema base, y el script se niega si alguna
    tabla que crea el archivo ya existe. Se aplica en una sola transacción: o entra todo o nada.
  * Con certificado CA, el SSL se verifica con verify-ca (ver irrigation_app.db).
  * Nunca imprime la contraseña.
"""

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from irrigation_app import db  # noqa: E402

SCHEMA_FILE = PROJECT_ROOT / "sql" / "create_schema.sql"
TARGET_DATABASE = "irrigation_app"
# Bases de mantenimiento donde conectarse para crear la nueva (según el servidor).
MAINTENANCE_DATABASES = ["postgres", "defaultdb"]
LIST_TABLES_SQL = (
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' ORDER BY 1"
)
CREATE_TABLE_PATTERN = re.compile(r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE | re.MULTILINE)


def load_config(env_file: Path) -> db.DbConfig:
    """Lee las variables DB_* del archivo y comprueba que sean las esperadas."""
    try:
        config = db.load_config(env_file)
    except db.DatabaseConfigError as error:
        sys.exit(str(error))
    if config.dbname != TARGET_DATABASE:
        sys.exit(f"DB_NAME vale '{config.dbname}' y debe ser '{TARGET_DATABASE}'. No se hace nada.")
    return config


def connect(config: db.DbConfig, database: str) -> psycopg.Connection:
    """Abre una conexión a la base indicada, con SSL verificado si hay certificado."""
    return db.connect(config, database=database, autocommit=True)


ALTER_TABLE_PATTERN = re.compile(r"^\s*ALTER\s+TABLE\s+(?:ONLY\s+)?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE | re.MULTILINE)


def tables_altered_by(sql_text: str) -> list[str]:
    """Nombres de las tablas que modifica un archivo SQL (sentencias ALTER TABLE)."""
    return ALTER_TABLE_PATTERN.findall(sql_text)


def tables_created_by(sql_text: str) -> list[str]:
    """Nombres de las tablas que crea un archivo SQL (sentencias CREATE TABLE)."""
    return CREATE_TABLE_PATTERN.findall(sql_text)


def create_database_if_missing(config: db.DbConfig) -> psycopg.Connection:
    """Conecta a la base del proyecto; si no existe, la crea desde una base de mantenimiento."""
    try:
        return connect(config, TARGET_DATABASE)
    except psycopg.OperationalError as error:
        if "does not exist" not in str(error):
            sys.exit(f"No se pudo conectar: {error}")
    for maintenance in MAINTENANCE_DATABASES:
        try:
            admin = connect(config, maintenance)
        except psycopg.OperationalError:
            continue
        with admin:
            admin.execute(f"CREATE DATABASE \"{TARGET_DATABASE}\" ENCODING 'UTF8'")
        print(f"Base {TARGET_DATABASE} creada.")
        return connect(config, TARGET_DATABASE)
    sys.exit("No se pudo crear la base: no hay una base de mantenimiento accesible.")


def list_tables(connection: psycopg.Connection) -> list[str]:
    return [row[0] for row in connection.execute(LIST_TABLES_SQL)]


def apply_migration(connection: psycopg.Connection, migration_file: Path) -> list[str]:
    """Aplica una migración a una base que ya tiene el esquema. Devuelve las tablas nuevas.

    Se niega si el archivo no crea ninguna tabla, si la base está vacía o si alguna de las tablas
    del archivo ya existe. Todo el archivo se ejecuta en una transacción.
    """
    sql_text = migration_file.read_text(encoding="utf-8")
    wanted = tables_created_by(sql_text)
    altered = tables_altered_by(sql_text)
    if not wanted and not altered:
        sys.exit(f"{migration_file.name} no crea ni modifica ninguna tabla: no se aplica.")
    existing = list_tables(connection)
    if not existing:
        sys.exit("La base está vacía: aplica primero el esquema base (sin --migration).")
    already = sorted(set(wanted) & set(existing))
    if already:
        sys.exit(f"Ya existen las tablas {', '.join(already)}: la migración parece aplicada. No se hace nada.")
    missing_base = sorted(set(altered) - set(existing) - set(wanted))
    if missing_base:
        sys.exit(f"La migración modifica tablas que no existen: {', '.join(missing_base)}.")
    try:
        with connection.transaction():
            connection.execute(sql_text)
    except (psycopg.errors.DuplicateColumn, psycopg.errors.DuplicateObject, psycopg.errors.DuplicateTable):
        sys.exit("Ya existen columnas u objetos de la migración: parece aplicada. No se hace nada.")
    created = sorted(set(list_tables(connection)) - set(existing))
    missing = sorted(set(wanted) - set(created))
    if missing:
        sys.exit(f"La migración no creó {', '.join(missing)}.")
    return created


def main() -> None:
    os.chdir(PROJECT_ROOT)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("env_file", nargs="?", default=".env", help="archivo de configuración (por defecto .env)")
    parser.add_argument("--migration", type=Path, help="archivo SQL de una migración que se aplica sobre una base ya creada")
    args = parser.parse_args()

    config = load_config(PROJECT_ROOT / args.env_file)
    print(f"Archivo: {args.env_file}")
    print(f"Destino: {config.host}:{config.port} (usuario {config.user})")

    if args.migration:
        migration_file = args.migration if args.migration.is_absolute() else PROJECT_ROOT / args.migration
        if not migration_file.exists():
            sys.exit(f"No existe la migración {args.migration}")
        try:
            connection = connect(config, TARGET_DATABASE)
        except psycopg.OperationalError as error:
            sys.exit(f"No se pudo conectar: {error}")
        with connection:
            created = apply_migration(connection, migration_file)
        print(f"Migración {migration_file.name} aplicada. Tablas nuevas: {', '.join(created) or 'ninguna'}.")
        return

    connection = create_database_if_missing(config)
    with connection:
        existing = list_tables(connection)
        if existing:
            sys.exit(
                f"La base ya tiene {len(existing)} tablas ({', '.join(existing)}). "
                "No se aplica el esquema para no pisar nada."
            )

        # Un solo execute con todo el archivo: separar por ';' no sirve porque los
        # comentarios y las funciones del SQL también los contienen.
        connection.execute(SCHEMA_FILE.read_text(encoding="utf-8"))

        tables = list_tables(connection)
        kinds = dict(
            connection.execute(
                "SELECT c.contype, count(*) FROM pg_constraint c "
                "JOIN pg_namespace n ON n.oid = c.connamespace "
                "WHERE n.nspname = 'public' GROUP BY c.contype"
            ).fetchall()
        )
        vector = connection.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()

    print(
        f"Esquema aplicado: {len(tables)} tablas, {kinds.get('f', 0)} claves foráneas, "
        f"{kinds.get('c', 0)} CHECK y {kinds.get('u', 0)} UNIQUE."
    )
    print("Extensión vector:", vector[0] if vector else "NO INSTALADA")
    print("Tablas:", ", ".join(tables))


if __name__ == "__main__":
    main()
