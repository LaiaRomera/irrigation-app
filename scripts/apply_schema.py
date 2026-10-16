"""Crea la base de datos del proyecto y aplica sql/create_schema.sql.

Uso (desde la raíz del proyecto):
    .venv/Scripts/python scripts/apply_schema.py             # usa .env (Aiven)
    .venv/Scripts/python scripts/apply_schema.py .env.docker

Medidas de seguridad:
  * Solo actúa sobre la base `irrigation_app`: se niega si DB_NAME es otra.
  * Si la base ya tiene tablas, no hace nada (no pisa datos).
  * Nunca imprime la contraseña.
"""

import sys
from pathlib import Path

import pymysql
from dotenv import dotenv_values
from pymysql.constants import CLIENT

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = PROJECT_ROOT / "sql" / "create_schema.sql"
TARGET_DATABASE = "irrigation_app"
REQUIRED_KEYS = ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD"]


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


def connect(config: dict) -> pymysql.connections.Connection:
    """Abre la conexión sin elegir base de datos y con varias sentencias por llamada."""
    ssl_ca = config.get("DB_SSL_CA")
    ssl_options = None
    if ssl_ca:
        ca_path = Path(ssl_ca)
        if not ca_path.is_absolute():
            ca_path = PROJECT_ROOT / ca_path
        if not ca_path.exists():
            sys.exit(f"No se encuentra el certificado {ca_path}")
        ssl_options = {"ca": str(ca_path)}
    return pymysql.connect(
        host=config["DB_HOST"],
        port=int(config["DB_PORT"]),
        user=config["DB_USER"],
        password=config["DB_PASSWORD"],
        ssl=ssl_options,
        charset="utf8mb4",
        connect_timeout=15,
        client_flag=CLIENT.MULTI_STATEMENTS,
    )


def main() -> None:
    env_name = sys.argv[1] if len(sys.argv) > 1 else ".env"
    config = load_config(PROJECT_ROOT / env_name)
    print(f"Archivo: {env_name}")
    print(f"Destino: {config['DB_HOST']}:{config['DB_PORT']} (usuario {config['DB_USER']})")

    try:
        connection = connect(config)
    except pymysql.MySQLError as error:
        sys.exit(f"No se pudo conectar: {error}")

    with connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW DATABASES")
            databases = [row[0] for row in cursor.fetchall()]
            if TARGET_DATABASE in databases:
                print(f"La base {TARGET_DATABASE} ya existe.")
            else:
                cursor.execute(
                    f"CREATE DATABASE `{TARGET_DATABASE}` CHARACTER SET utf8mb4"
                )
                print(f"Base {TARGET_DATABASE} creada.")

            cursor.execute(f"USE `{TARGET_DATABASE}`")
            cursor.execute("SHOW TABLES")
            existing = [row[0] for row in cursor.fetchall()]
            if existing:
                sys.exit(
                    f"La base ya tiene {len(existing)} tablas ({', '.join(existing)}). "
                    "No se aplica el esquema para no pisar nada."
                )

            # Un solo execute con todo el archivo: separar por ';' no sirve porque
            # los comentarios del SQL también los contienen.
            cursor.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
            while cursor.nextset():
                pass

            cursor.execute("SHOW TABLES")
            tables = sorted(row[0] for row in cursor.fetchall())
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL",
                (TARGET_DATABASE,),
            )
            foreign_keys = cursor.fetchone()[0]

    print(f"Esquema aplicado: {len(tables)} tablas y {foreign_keys} claves foráneas.")
    print("Tablas:", ", ".join(tables))


if __name__ == "__main__":
    main()
