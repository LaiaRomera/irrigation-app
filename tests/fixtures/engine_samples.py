"""Entradas y resultados del motor de ejemplo para el informe (sintéticos: cumplen los contratos C1, C2, C3 y C5).

Los usan las pruebas de `reports/` y `make_sample_reports.py`. No hay datos reales ni de la empresa.
Se apoyan en `tests/irrigation_engine_factory.py`, de AgronomoFAO56.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from irrigation_engine_factory import CROSS_ON_DAY_4, WET, engine_input, plot_with, weather

from irrigation_app.irrigation_engine import EngineInput, EngineResult, run_engine
from irrigation_app.reports.metrics import HorizonError

FIXTURES_DIR = Path(__file__).parent
SYNTHETIC_TEMPLATE_DIR = FIXTURES_DIR / "agrodevices_template_sintetica"
SYNTHETIC_METRICS_CSV = FIXTURES_DIR / "metrics_sintetico.csv"

PLOT_LABEL = "Parcela de referencia 1168 (manzano, goteo)"
DRY = {k: 62.0 - 0.5 * k for k in range(1, 8)}  # ya bajo el umbral desde el día 1


def typical_error() -> tuple[HorizonError, ...]:
    """Error típico sintético (puntos del índice), creciente con el horizonte."""
    return tuple(
        HorizonError(horizon_days=h, mae=m, rmse=r, n=1000)
        for h, m, r in zip(range(1, 8), (0.66, 0.83, 1.04, 1.16, 1.29, 1.42, 1.51), (1.1, 1.4, 1.7, 1.9, 2.1, 2.3, 2.4))
    )


def sample_inputs() -> dict[str, EngineInput]:
    december = dt.date(2026, 12, 15)
    return {
        "cruza_dia_4": engine_input(CROSS_ON_DAY_4),
        "regar_hoy": engine_input(DRY),
        "aplazar_por_lluvia": engine_input(DRY, wx=weather(rain_by_day={1: 8.0})),
        "suelo_humedo": engine_input(WET),
        "fuera_de_campana": engine_input(CROSS_ON_DAY_4, origin=december, wx=weather(december)),
        "datos_insuficientes": engine_input(CROSS_ON_DAY_4, plot=plot_with(crop=None, status="candidate")),
    }


def sample_results() -> dict[str, EngineResult]:
    return {name: run_engine(inp) for name, inp in sample_inputs().items()}
