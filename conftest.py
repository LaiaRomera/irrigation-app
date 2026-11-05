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
