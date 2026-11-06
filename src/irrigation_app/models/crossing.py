"""Métrica de decisión de la ronda 2: ¿acierta el día en que el suelo cruza el umbral de riego?

El motor decide regar cuando el agotamiento `D = (superior − M) / (superior − inferior)` supera `p`,
donde `M` es la **media de las profundidades de la zona radicular**. Eso equivale a que la media
caiga por debajo de un nivel `M* = superior − p × (superior − inferior)` en el índice de 0 a 100.

Versión honesta de la métrica (todo declarado):
- **Caso**: una unidad y un día de origen cuya media de la zona radicular, ese día, está **por encima**
  de `M*` (aún no hay que regar), con los siete horizontes observados y todas las profundidades de la
  zona presentes en cada uno. Como la tabla de entrenamiento quita los periodos con riego marcado, los
  casos son periodos **sin riego** de siete días: es justo la condición «sin riego futuro» del modelo,
  pero deja fuera los periodos en que alguien regó.
- **Cruce real**: el primer día `h` (1 a 7) en que la media real queda por debajo de `M*`. Si no
  ocurre en la semana, no hay cruce.
- **Cruce previsto**: lo mismo con la media de las profundidades previstas (índice de origen + cambio
  previsto de cada profundidad). La persistencia simple nunca cruza (prevé que nada cambia).
- Resultados por método: día exacto, ±1 día, día equivocado (previó otro día), **omisión** (hay cruce
  real y el método no prevé ninguno) y **falsa alarma** (el método prevé un cruce y no lo hay).

Los anclajes (`superior`, `inferior`) solo están confirmados para la unidad 1168. Para las demás se
usa una aproximación declarada: percentiles de la propia unidad con datos **de entrenamiento**
(anteriores al corte), `proxy_anchors`.
"""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
import pandas as pd

HORIZONS = tuple(range(1, 8))
DEFAULT_P = 0.5  # fracción de agotamiento del manzano (FAO-56, tabla 22), la del motor
PROXY_UPPER_PERCENTILE = 95
PROXY_LOWER_PERCENTILE = 5
MIN_PROXY_DAYS = 60  # una unidad con menos días de entrenamiento no recibe anclajes aproximados

RESULT_COLUMNS = [
    "method", "p", "n_units", "n_cases", "n_events", "n_no_events", "exact", "exact_pct", "within_1", "within_1_pct",
    "wrong_day", "wrong_day_pct", "omission", "omission_pct", "false_alarm", "false_alarm_pct",
]


def threshold_index(upper: float, lower: float, p: float = DEFAULT_P) -> float:
    """Nivel `M*` por debajo del cual el agotamiento supera `p` (el motor riega con D > p)."""
    if not upper > lower:
        raise ValueError("el anclaje superior debe ser mayor que el inferior")
    return upper - p * (upper - lower)


def proxy_anchors(
    zone_mean: pd.Series,
    upper_percentile: float = PROXY_UPPER_PERCENTILE,
    lower_percentile: float = PROXY_LOWER_PERCENTILE,
    min_days: int = MIN_PROXY_DAYS,
) -> tuple[float, float] | None:
    """Anclajes aproximados (superior, inferior) de una unidad a partir de su propia serie de entrenamiento.

    `zone_mean` es la media diaria de la zona radicular, solo de días anteriores al corte. Si hay menos
    de `min_days` días o los percentiles no se separan, devuelve None (la unidad no se evalúa).
    """
    values = zone_mean.dropna().to_numpy(dtype=float)
    if values.size < min_days:
        return None
    upper, lower = np.percentile(values, [upper_percentile, lower_percentile])
    return (float(upper), float(lower)) if upper > lower else None


def zone_depths_of(units: pd.DataFrame) -> dict[int, tuple[int, ...]]:
    """Profundidades de la zona radicular de cada unidad: las marcadas como incluidas en la media."""
    zones: dict[int, tuple[int, ...]] = {}
    for _, unit in units.iterrows():
        depths = tuple(
            sorted(
                int(unit[f"depth_cm_{i}"])
                for i in range(1, 6)
                if pd.notna(unit[f"depth_cm_{i}"]) and bool(unit[f"in_average_{i}"])
            )
        )
        if depths:
            zones[int(unit["unit_id"])] = depths
    return zones


def zone_mean_by_unit(
    snapshots: pd.DataFrame,
    zones: Mapping[int, Sequence[int]],
    installation: pd.DataFrame,
    last_day: pd.Timestamp,
) -> dict[int, pd.Series]:
    """Media diaria (05:00 UTC) de la zona radicular de cada unidad hasta `last_day`, sin días de instalación.

    Un día solo cuenta si están todas las profundidades de la zona. Se usa para los anclajes aproximados,
    así que `last_day` es el corte del reparto: nada posterior entra en ellos.
    """
    from irrigation_app.irrigation_marks import installation_mask

    result: dict[int, pd.Series] = {}
    clean = snapshots[~installation_mask(snapshots, installation) & (snapshots["day"] <= last_day)]
    for unit_id, unit in clean.groupby("unit_id"):
        depths = zones.get(int(unit_id))
        if not depths:
            continue
        unit = unit[unit["depth_cm"].isin(depths)]
        grouped = unit.groupby("day")["moisture_index"]
        mean = grouped.mean()
        result[int(unit_id)] = mean[grouped.count() == len(depths)].sort_index()
    return result


def build_cases(
    rows: pd.DataFrame,
    zones: Mapping[int, Sequence[int]],
    predicted_changes: Mapping[str, np.ndarray],
) -> dict:
    """Pasa filas por profundidad a casos por (unidad, origen) con la media de la zona radicular.

    `rows` tiene `unit_id`, `depth_cm`, `origin_day`, `horizon_days`, `base_index` y `target_index`;
    `predicted_changes` da, por método, el cambio previsto alineado con `rows`. Un caso necesita los
    horizontes 1 a 7 y todas las profundidades de la zona en cada uno. Devuelve `units`, `base` (n,),
    `actual` (n, 7) y `predicted` (método -> (n, 7)).
    """
    frame = rows[["unit_id", "depth_cm", "origin_day", "horizon_days", "base_index", "target_index"]].copy()
    for method, change in predicted_changes.items():
        frame[f"pred__{method}"] = frame["base_index"].to_numpy(dtype=float) + np.asarray(change, dtype=float)
    in_zone = [d in zones.get(u, ()) for u, d in zip(frame["unit_id"], frame["depth_cm"])]
    frame = frame[in_zone]
    value_columns = ["base_index", "target_index", *[c for c in frame.columns if c.startswith("pred__")]]
    grouped = frame.groupby(["unit_id", "origin_day", "horizon_days"])
    means = grouped[value_columns].mean()
    means["n_depths"] = grouped.size()
    means = means.reset_index()
    needed = means["unit_id"].map(lambda u: len(zones[u]))
    means = means[means["n_depths"] == needed]
    complete = means.groupby(["unit_id", "origin_day"])["horizon_days"].transform("nunique") == len(HORIZONS)
    means = means[complete]

    def wide(column: str) -> pd.DataFrame:
        return means.pivot(index=["unit_id", "origin_day"], columns="horizon_days", values=column).reindex(columns=list(HORIZONS))

    actual = wide("target_index")
    return {
        "units": actual.index.get_level_values("unit_id").to_numpy(),
        "base": wide("base_index")[1].to_numpy(dtype=float),
        "actual": actual.to_numpy(dtype=float),
        "predicted": {m: wide(f"pred__{m}").to_numpy(dtype=float) for m in predicted_changes},
    }


def first_crossing_day(levels: np.ndarray, threshold: np.ndarray) -> np.ndarray:
    """Primer día (1 a 7) con nivel por debajo del umbral; 0 si no cruza. `levels` es (n, 7)."""
    below = levels < np.asarray(threshold, dtype=float)[:, None]
    return np.where(below.any(axis=1), below.argmax(axis=1) + 1, 0)


def evaluate_crossings(cases: dict, thresholds: Mapping[int, float], p: float = DEFAULT_P) -> pd.DataFrame:
    """Acierto del día de cruce por método. `thresholds` da `M*` por unidad; sin umbral, la unidad se omite.

    Solo cuentan los casos que parten por encima del umbral. Los porcentajes de acierto y de omisión
    son sobre los cruces reales; el de falsa alarma, sobre las semanas sin cruce real.
    """
    units = cases["units"]
    threshold = np.array([thresholds.get(int(u), np.nan) for u in units], dtype=float)
    keep = ~np.isnan(threshold) & (cases["base"] >= threshold)
    threshold = threshold[keep]
    real_day = first_crossing_day(cases["actual"][keep], threshold)
    is_event = real_day > 0
    n_events, n_no_events = int(is_event.sum()), int((~is_event).sum())

    def pct(count: int, total: int) -> float:
        return 100.0 * count / total if total else float("nan")

    rows = []
    for method, levels in cases["predicted"].items():
        predicted_day = first_crossing_day(levels[keep], threshold)
        predicted = predicted_day > 0
        exact = int((is_event & (predicted_day == real_day)).sum())
        within_1 = int((is_event & predicted & (np.abs(predicted_day - real_day) <= 1)).sum())
        omission = int((is_event & ~predicted).sum())
        false_alarm = int((~is_event & predicted).sum())
        wrong_day = n_events - within_1 - omission
        rows.append(
            {
                "method": method, "p": p, "n_units": int(len(np.unique(units[keep]))), "n_cases": int(keep.sum()),
                "n_events": n_events, "n_no_events": n_no_events,
                "exact": exact, "exact_pct": pct(exact, n_events),
                "within_1": within_1, "within_1_pct": pct(within_1, n_events),
                "wrong_day": wrong_day, "wrong_day_pct": pct(wrong_day, n_events),
                "omission": omission, "omission_pct": pct(omission, n_events),
                "false_alarm": false_alarm, "false_alarm_pct": pct(false_alarm, n_no_events),
            }
        )
    return pd.DataFrame(rows, columns=RESULT_COLUMNS)
