"""Comprueba que el marcador `integration` existe y que se salta sola sin PostgreSQL (ver conftest.py)."""

import socket

import pytest


@pytest.mark.integration
def test_postgres_is_reachable_when_integration_runs():
    with socket.create_connection(("localhost", 5433), timeout=1):
        pass
