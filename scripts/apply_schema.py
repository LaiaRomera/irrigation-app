"""Crea la base de datos del proyecto en PostgreSQL y aplica sql/create_schema.sql.

Uso (desde la raíz del proyecto):
    .venv/Scripts/python scripts/apply_schema.py                  # usa .env (Aiven)
    .venv/Scripts/python scripts/apply_schema.py .env.docker-pg   # PostgreSQL local

Medidas de seguridad:
  * Solo actúa sobre la base `irrigation_app`: se niega si DB_NAME es otra.
  * Si la base ya tiene tablas, no hace nada (no pisa datos).
  * Con certificado CA, el SSL se verifica con verify-ca (ver connect).
  * Nunca imprime la contraseña.
"""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = PROJECT_ROOT / "sql" / "create_schema.sql"
TARGET_DATABASE = "irrigation_app"
REQUIRED_KEYS = ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD"]
# Bases de mantenimiento donde conectarse para crear la nueva (según el servidor).
MAINTENANCE_DATABASES = ["postgres", "defaultdb"]


def load_config(env_file: Path) -> dict:
    """Lee las variables DB_* del archivo y comprueba que sean las esperadas."""
    if not env_file.exists():
        sys.exit(f"No existe el archivo {env_file}")
    config = dotenv_values(env_file)
    missing = [key for key in REQUIRED_KEYS if not config.get(key)]
    if missing:
        sys.exit(f"Faltan variables en {env_file.name}: {', '.join(missing)}")
    if config.get("DB_NAME") != TARGET_DATABASE:
        sys.exit(
            f"DB_NAME vale '{config.get('DB_NAME')}' y debe ser '{TARGET_DATABASE}'. "
            "No se hace nada."
        )
    return config


def connect(config: dict, database: str) -> psycopg.Connection:
    """Abre una conexión a la base indicada, con SSL verificado si hay certificado."""
    options = {
        "host": config["DB_HOST"],
        "port": int(config["DB_PORT"]),
        "user": config["DB_USER"],
        "password": config["DB_PASSWORD"],
        "dbname": database,
        "connect_timeout": 15,
        "autocommit": True,
    }
    ssl_ca = config.get("DB_SSL_CA")
    if ssl_ca:
        ca_path = Path(ssl_ca)
        if not ca_path.is_absolute():
            ca_path = PROJECT_ROOT / ca_path
        if not ca_path.exists():
            sys.exit(f"No se encuentra el certificado {ca_path}")
        # verify-ca comprueba que el certificado lo firma la CA de Aiven; verify-full
        # (que además compara el nombre del servidor) falla en Windows con "certificate
        # contains IP address with invalid length 16" por un fallo de la librería.
        options["sslmode"] = "verify-ca"
        # Ruta relativa a la raíz del proyecto (main hace chdir): libpq no abre el
        # certificado si la ruta completa lleva caracteres no ASCII, como la "ó" de
        # "Programación".
        options["sslrootcert"] = os.path.relpath(ca_path, PROJECT_ROOT)
    return psycopg.connect(**options)


def create_database_if_missing(config: dict) -> psycopg.Connection:
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
            admin.execute(f'CREATE DATABASE "{TARGET_DATABASE}" ENCODING \'UTF8\'')
        print(f"Base {TARGET_DATABASE} creada.")
        return connect(config, TARGET_DATABASE)
    sys.exit("No se pudo crear la base: no hay una base de mantenimiento accesible.")


def main() -> None:
    os.chdir(PROJECT_ROOT)
    env_name = sys.argv[1] if len(sys.argv) > 1 else ".env"
    config = load_config(PROJECT_ROOT / env_name)
    print(f"Archivo: {env_name}")
    print(f"Destino: {config['DB_HOST']}:{config['DB_PORT']} (usuario {config['DB_USER']})")

    connection = create_database_if_missing(config)
    with connection:
        existing = [
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"
            )
        ]
        if existing:
            sys.exit(
                f"La base ya tiene {len(existing)} tablas ({', '.join(existing)}). "
                "No se aplica el esquema para no pisar nada."
            )

        # Un solo execute con todo el archivo: separar por ';' no sirve porque los
        # comentarios y las funciones del SQL también los contienen.
        connection.execute(SCHEMA_FILE.read_text(encoding="utf-8"))

        tables = [
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"
            )
        ]
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
