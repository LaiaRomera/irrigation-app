"""Las pruebas marcadas `integration` se saltan solas si no hay PostgreSQL local (Docker `irrigation-pg`, puerto 5433)."""

import socket

import pytest

POSTGRES_HOST = "localhost"
POSTGRES_PORT = 5433


def _postgres_available() -> bool:
    try:
        with socket.create_connection((POSTGRES_HOST, POSTGRES_PORT), timeout=1):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config, items):
    integration = [item for item in items if item.get_closest_marker("integration")]
    if integration and not _postgres_available():
        skip = pytest.mark.skip(reason=f"no hay PostgreSQL en {POSTGRES_HOST}:{POSTGRES_PORT}")
        for item in integration:
            item.add_marker(skip)


@pytest.fixture
def pg_connection():
    """Conexión al PostgreSQL local (`.env.docker-pg`) dentro de un esquema temporal con todo `create_schema.sql`.

    Cada prueba trabaja en su propio esquema, que se borra al terminar: no toca las tablas del esquema
    `public` ni deja filas. Con `autocommit` desactivado, como en el código de producción.
    """
    import uuid
    from pathlib import Path

    from irrigation_app import db

    schema = f"test_{uuid.uuid4().hex[:10]}"
    admin = db.connect(db.load_config(".env.docker-pg"), autocommit=True)
    admin.execute(f"CREATE SCHEMA {schema}")
    connection = None
    try:
        admin.execute(f"SET search_path TO {schema}, public")
        admin.execute((Path(__file__).parent / "sql" / "create_schema.sql").read_text(encoding="utf-8"))
        connection = db.connect(db.load_config(".env.docker-pg"))
        connection.execute(f"SET search_path TO {schema}, public")
        connection.commit()
        yield connection
    finally:
        if connection is not None:
            connection.close()
        admin.execute(f"DROP SCHEMA {schema} CASCADE")
        admin.close()
