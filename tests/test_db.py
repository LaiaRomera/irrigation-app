"""Configuración y opciones de conexión de db.py, sin conectarse a ninguna base."""

from pathlib import Path

import pytest

from irrigation_app import db


def write_env(path: Path, **overrides) -> Path:
    values = {"DB_HOST": "127.0.0.1", "DB_PORT": "5433", "DB_USER": "postgres", "DB_PASSWORD": "secreto-de-prueba", "DB_NAME": "irrigation_app"}
    values.update(overrides)
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8")
    return path


def test_load_config_reads_the_given_env_file(tmp_path):
    config = db.load_config(write_env(tmp_path / ".env.test"))
    assert (config.host, config.port, config.user, config.dbname) == ("127.0.0.1", 5433, "postgres", "irrigation_app")
    assert config.ssl_ca is None


def test_the_password_never_appears_in_repr_or_description(tmp_path):
    config = db.load_config(write_env(tmp_path / ".env.test"))
    assert "secreto-de-prueba" not in repr(config)
    assert "secreto-de-prueba" not in db.describe(config)


def test_missing_variables_are_reported_by_name_without_values(tmp_path):
    path = tmp_path / ".env.test"
    path.write_text("DB_HOST=x\nDB_PASSWORD=secreto-de-prueba\n", encoding="utf-8")
    with pytest.raises(db.DatabaseConfigError) as error:
        db.load_config(path)
    assert "DB_PORT" in str(error.value) and "DB_USER" in str(error.value)
    assert "secreto-de-prueba" not in str(error.value)


def test_missing_file_and_bad_port_are_clear_errors(tmp_path):
    with pytest.raises(db.DatabaseConfigError):
        db.load_config(tmp_path / "no-existe.env")
    with pytest.raises(db.DatabaseConfigError, match="DB_PORT"):
        db.load_config(write_env(tmp_path / ".env.bad", DB_PORT="abc"))


def test_environment_variables_are_used_without_an_env_file(monkeypatch):
    monkeypatch.setattr(db, "DEFAULT_ENV_FILE", Path("no-existe.env"))
    config = db.load_config(environ={"DB_HOST": "h", "DB_PORT": "1", "DB_USER": "u", "DB_PASSWORD": "p"})
    assert (config.host, config.port, config.dbname) == ("h", 1, "irrigation_app")  # base por defecto


def test_ssl_certificate_is_passed_relative_to_the_project_root(tmp_path, monkeypatch):
    certificate = db.PROJECT_ROOT / "ca.pem"
    if not certificate.exists():
        pytest.skip("no hay ca.pem local")
    config = db.load_config(write_env(tmp_path / ".env.test", DB_SSL_CA="ca.pem"))
    options = db.connection_options(config)
    assert options["sslmode"] == "verify-ca"
    assert options["sslrootcert"] == "ca.pem"  # relativa: libpq no abre rutas con la «ó» de Programación


def test_missing_certificate_is_an_error(tmp_path):
    with pytest.raises(db.DatabaseConfigError, match="certificado"):
        db.load_config(write_env(tmp_path / ".env.test", DB_SSL_CA="no-existe.pem"))


def test_connection_options_without_ssl_have_no_ssl_keys(tmp_path):
    options = db.connection_options(db.load_config(write_env(tmp_path / ".env.test")), database="otra")
    assert options["dbname"] == "otra" and "sslmode" not in options


@pytest.mark.integration
def test_connects_to_the_local_database_and_runs_select_1():
    with db.connect(db.load_config(".env.docker-pg")) as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)
