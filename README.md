# irrigation-app

Plataforma web de **recomendaciones de riego explicables** para parcelas agrícolas. Combina datos de sensores de suelo (Agrodevices) con la previsión y el histórico meteorológico de Open-Meteo, y explica **cuánto regar, cuándo y por qué** a partir de una recomendación calculada. No acciona válvulas.

Alcance en tres capas: un **modelo de deep learning predice** la humedad del suelo, un **motor determinista decide** la recomendación y un **modelo de lenguaje ajustado (fine-tuning) explica** el resultado, de forma integrada y automática. Incluye informes diario y semanal, alertas agronómicas y acceso de cada usuario a sus parcelas. Opcional: corrección del sesgo meteorológico. Fuera del MVP: detector de anomalías en sensores. Todo gratuito y en el ordenador de la autora.

## Estado (14 de octubre de 2026)

El proyecto está en **fase de diseño**: todavía no hay web, tareas ni motor.

| Pieza | Estado |
|---|---|
| Arquitectura y decisiones | Documentadas |
| Esquema de la base de datos (10 tablas) | Hecho y validado en MySQL local y en Aiven |
| Login con Auth0 y Streamlit | Probado en local; pendiente en Streamlit Community Cloud |
| Conector de Open-Meteo | Por hacer (se adapta el del servicio antiguo) |
| Modelo de humedad del suelo, motor, modelo de lenguaje, informes, alertas, tareas diarias, web | Por hacer |

## Documentación

| Documento | Para qué |
|---|---|
| [docs/arquitectura.md](docs/arquitectura.md) | Procesos, flujos, módulos, pantallas, seguridad y alojamiento |
| [docs/base_de_datos.md](docs/base_de_datos.md) | Diccionario de datos, relaciones y restricciones |
| [docs/erd.md](docs/erd.md) | Diagrama entidad-relación |
| [docs/puesta_en_marcha.md](docs/puesta_en_marcha.md) | Cómo montar el entorno, la base de datos y la prueba de login |
| [docs/decisiones.md](docs/decisiones.md) | Diario de decisiones: acordado, provisional, mejoras futuras y pendientes |
| [docs/glosario.md](docs/glosario.md) | Equivalencias inglés–español |
| [docs/deliverations/02_datos_y_preparacion.md](docs/deliverations/02_datos_y_preparacion.md) | Entrega 02 del curso: datos, fuentes y flujo Bronze → Silver → Gold (borrador) |
| [sql/create_schema.sql](sql/create_schema.sql) | Esquema ejecutable |

## Puesta en marcha rápida

Requiere Python 3.14 y una base MySQL 8.4 (Aiven o Docker). Detalle completo en [docs/puesta_en_marcha.md](docs/puesta_en_marcha.md).

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements.txt
```

Copia `.env.example` a `.env`, rellénalo (la base debe llamarse `irrigation_app`) y crea el esquema:

```bash
.venv/Scripts/python scripts/apply_schema.py
```

El script solo actúa sobre `irrigation_app` y no hace nada si ya tiene tablas.

## Estructura

| Carpeta o archivo | Contenido |
|---|---|
| `src/irrigation_app/` | Paquete Python único con toda la lógica (aún solo el esqueleto) |
| `scripts/` | Herramientas de operación (`apply_schema.py`) |
| `sql/` | Esquema de la base de datos |
| `docs/` | Documentación |
| `tests/` | Pruebas (todavía vacío) |
| `data/` | Datos locales de trabajo, fuera de git |
| `.env.example` | Plantilla de configuración; el `.env` real nunca se sube |
| `requirements.txt` | Dependencias con versión fijada |

## Convenciones

- **Idioma:** los identificadores (variables, funciones, módulos, tablas y columnas) van en inglés; comentarios, docstrings, interfaz, informes, mensajes de error y documentación van en español. Excepción acordada: las columnas meteorológicas conservan los nombres de la tabla de sensores de Agrodevices (`tMax`, `rhMedia`, `eto`...). El glosario recoge las equivalencias.
- **Secretos:** nunca en el código ni en git. `.env`, `.env.*` (salvo `.env.example`), `*.pem` y `.streamlit/secrets.toml` están ignorados.
- **Sin datos inventados:** columnas de sensores, unidades, calibraciones, parámetros agronómicos y umbrales que aún no se conocen quedan marcados como pendientes.
- **Un cambio pequeño cada vez**, comprobado antes de seguir.

## Servicio anterior

El proyecto parte de un servicio meteorológico previo (`agrodevices-meteo-app`) del que se reutilizan y adaptan piezas como el conector de Open-Meteo. No se depende de su API ni de su base de datos. Detalle en [docs/arquitectura.md](docs/arquitectura.md), sección 10.
