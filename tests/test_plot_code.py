"""Código de parcela (plot_code) y migración 002: generación, formato y restricciones en PostgreSQL."""

import importlib.util
from pathlib import Path

import psycopg
import pytest

from irrigation_app import plot_code

ROOT = Path(__file__).resolve().parent.parent
MIGRATION_FILE = ROOT / "sql" / "migrations" / "002_plot_code_and_interval.sql"
MIGRATION = MIGRATION_FILE.read_text(encoding="utf-8")
SCHEMA = (ROOT / "sql" / "create_schema.sql").read_text(encoding="utf-8")

_spec = importlib.util.spec_from_file_location("apply_schema", ROOT / "scripts" / "apply_schema.py")
apply_schema = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apply_schema)


def test_generated_codes_have_the_right_format_and_no_ambiguous_characters():
    codes = {plot_code.generate_plot_code() for _ in range(500)}
    assert len(codes) == 500  # aleatorios: no se repiten ni son secuenciales
    for code in codes:
        assert len(code) == 8 and plot_code.is_valid_plot_code(code)
        assert not set(code) & set("01ILO")


def test_invalid_codes_are_rejected():
    for bad in ("", "ABC", "ABCDEFGH1", "abcdefgh", "ABCDEFG0", "ABCDEFGI"):
        assert not plot_code.is_valid_plot_code(bad)


def test_the_regex_in_the_sql_uses_the_same_alphabet():
    assert f"[{plot_code.ALPHABET}]{{{plot_code.LENGTH}}}" in MIGRATION


def test_create_schema_contains_migration_002_verbatim():
    assert MIGRATION[MIGRATION.index("-- Código con el que"):] in SCHEMA


def test_the_alter_statements_are_detected_as_a_migration_on_plot():
    assert apply_schema.tables_created_by(MIGRATION) == []
    assert apply_schema.tables_altered_by(MIGRATION) == ["plot", "plot"]


def _plot(connection, **extra):
    user = connection.execute(
        "INSERT INTO app_user (email, name) VALUES (%s, 'T') ON CONFLICT (email) DO UPDATE SET name = 'T' RETURNING user_id",
        ("t@example.com",),
    ).fetchone()[0]
    columns = ", ".join(["user_id", "name", "latitude", "longitude", "timezone", *extra])
    marks = ", ".join(["%s"] * (5 + len(extra)))
    return connection.execute(
        f"INSERT INTO plot ({columns}) VALUES ({marks}) RETURNING plot_id", (user, "P", 41.9, 0.2, "Europe/Madrid", *extra.values())
    ).fetchone()[0]


@pytest.mark.integration
def test_plot_code_is_unique_and_checked(pg_connection):
    _plot(pg_connection, plot_code="ABCDEFGH")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _plot(pg_connection, plot_code="ABCDEFGH")
    pg_connection.rollback()
    for bad in ("abcdefgh", "ABCDEFG0", "SHORT"):
        with pytest.raises(psycopg.errors.CheckViolation):
            _plot(pg_connection, plot_code=bad)
        pg_connection.rollback()
    _plot(pg_connection)  # el código es opcional (parcelas anteriores a la migración): varios vacíos permitidos
    _plot(pg_connection)


@pytest.mark.integration
@pytest.mark.parametrize("days,ok", [(1, True), (3, True), (7, True), (None, True), (0, False), (8, False)])
def test_irrigation_interval_is_between_1_and_7(pg_connection, days, ok):
    if ok:
        _plot(pg_connection, irrigation_interval_days=days)
    else:
        with pytest.raises(psycopg.errors.CheckViolation):
            _plot(pg_connection, irrigation_interval_days=days)


@pytest.mark.integration
def test_migration_002_applies_to_a_base_without_the_columns_and_refuses_twice(pg_connection):
    pg_connection.execute("ALTER TABLE plot DROP COLUMN plot_code, DROP COLUMN irrigation_interval_days")
    pg_connection.commit()
    assert apply_schema.apply_migration(pg_connection, MIGRATION_FILE) == []
    _plot(pg_connection, plot_code="ABCDEFGH", irrigation_interval_days=3)
    pg_connection.commit()
    with pytest.raises(SystemExit, match="parece aplicada"):
        apply_schema.apply_migration(pg_connection, MIGRATION_FILE)
