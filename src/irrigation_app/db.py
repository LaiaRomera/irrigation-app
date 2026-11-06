"""Conexión a PostgreSQL (tarea 4.1).

La configuración sale de variables de entorno o de un archivo `.env` (nunca del código):
`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` y, si el servidor exige SSL (Aiven),
`DB_SSL_CA` con la ruta del certificado.

Reglas de carga:
- Con `env_file`, solo se lee ese archivo (por ejemplo `.env.docker-pg` para el PostgreSQL local).
- Sin él, las variables de entorno mandan sobre el `.env` de la raíz del proyecto.

La contraseña no se imprime nunca: ni en `repr`, ni en los mensajes de error.

Con certificado CA se usa `sslmode=verify-ca` (con `verify-full` falla en Windows por un fallo de la
librería con las direcciones IP). La ruta del certificado se pasa **relativa a la raíz del proyecto**,
porque libpq no abre el archivo si la ruta completa lleva caracteres no ASCII, como la «ó» de
«Programación»; `connect` se encarga de cambiar al directorio de la raíz solo el tiempo de abrir la
conexión.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Mapping

import psycopg
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_DATABASE = "irrigation_app"
REQUIRED_KEYS = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD")
ENV_KEYS = (*REQUIRED_KEYS, "DB_NAME", "DB_SSL_CA")
CONNECT_TIMEOUT_SECONDS = 15


class DatabaseConfigError(RuntimeError):
    """Falta configuración de la base de datos o el archivo indicado no existe."""


@dataclass(frozen=True)
class DbConfig:
    """Datos de conexión. La contraseña no aparece en `repr`."""

    host: str
    port: int
    user: str
    password: str = field(repr=False)
    dbname: str = DEFAULT_DATABASE
    ssl_ca: Path | None = None  # certificado CA (ruta absoluta); None = sin SSL verificado


def load_config(
    env_file: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> DbConfig:
    """Lee la configuración (ver el encabezado del módulo para el orden de prioridad).

    `environ` permite inyectar un entorno en las pruebas; por defecto es `os.environ`.
    """
    if env_file is not None:
        path = Path(env_file)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            raise DatabaseConfigError(f"No existe el archivo de configuración {path.name}")
        values = {k: v for k, v in dotenv_values(path).items() if v is not None}
    else:
        values = {k: v for k, v in dotenv_values(DEFAULT_ENV_FILE).items() if v is not None} if DEFAULT_ENV_FILE.exists() else {}
        source = os.environ if environ is None else environ
        values.update({k: source[k] for k in ENV_KEYS if source.get(k)})

    missing = [key for key in REQUIRED_KEYS if not values.get(key)]
    if missing:
        raise DatabaseConfigError(f"Faltan variables de conexión: {', '.join(missing)}")
    try:
        port = int(values["DB_PORT"])
    except ValueError:
        raise DatabaseConfigError("DB_PORT debe ser un número") from None

    ssl_ca = None
    if values.get("DB_SSL_CA"):
        ssl_ca = Path(values["DB_SSL_CA"])
        if not ssl_ca.is_absolute():
            ssl_ca = PROJECT_ROOT / ssl_ca
        if not ssl_ca.exists():
            raise DatabaseConfigError(f"No se encuentra el certificado {ssl_ca.name}")
    return DbConfig(
        host=values["DB_HOST"],
        port=port,
        user=values["DB_USER"],
        password=values["DB_PASSWORD"],
        dbname=values.get("DB_NAME") or DEFAULT_DATABASE,
        ssl_ca=ssl_ca,
    )


def connection_options(config: DbConfig, database: str | None = None, autocommit: bool = False) -> dict:
    """Argumentos de `psycopg.connect`. La ruta del certificado se deja relativa a la raíz del proyecto."""
    options: dict = {
        "host": config.host,
        "port": config.port,
        "user": config.user,
        "password": config.password,
        "dbname": database or config.dbname,
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        "autocommit": autocommit,
    }
    if config.ssl_ca is not None:
        options["sslmode"] = "verify-ca"
        options["sslrootcert"] = os.path.relpath(config.ssl_ca, PROJECT_ROOT)
    return options


@contextmanager
def _in_project_root() -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(PROJECT_ROOT)
    try:
        yield
    finally:
        os.chdir(previous)


def connect(
    config: DbConfig | None = None,
    *,
    database: str | None = None,
    autocommit: bool = False,
) -> psycopg.Connection:
    """Abre una conexión. Sin `config`, la carga con `load_config()`.

    Si el certificado es relativo, la conexión se abre con el directorio de trabajo en la raíz del
    proyecto (ver el encabezado). Con `autocommit=False` hay que confirmar con `commit()` o usar la
    conexión en un `with`.
    """
    config = config or load_config()
    options = connection_options(config, database, autocommit)
    if config.ssl_ca is None:
        return psycopg.connect(**options)
    with _in_project_root():
        return psycopg.connect(**options)


def describe(config: DbConfig) -> str:
    """Destino en texto para mostrar al usuario: sin contraseña."""
    return f"{config.host}:{config.port}/{config.dbname} (usuario {config.user})"
