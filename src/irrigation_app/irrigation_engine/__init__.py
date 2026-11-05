"""Motor de riego FAO-56 (función pura, sin red ni base de datos).

- `reference_values`: tablas FAO-56 con la fuente de cada cifra.
- `inputs`: `EngineInput` (parcela, predicciones de humedad por sonda y día, meteorología).
- `calculation`: `run_engine(EngineInput) -> EngineResult`.
- `result`: `EngineResult` (plan de 7 días, dosis en mm, factores, limitaciones, sensibilidad).
"""

from irrigation_app.irrigation_engine.calculation import run_engine
from irrigation_app.irrigation_engine.inputs import EngineInput, SensorPrediction, WeatherDay
from irrigation_app.irrigation_engine.result import EngineResult

__all__ = ["EngineInput", "EngineResult", "SensorPrediction", "WeatherDay", "run_engine"]
