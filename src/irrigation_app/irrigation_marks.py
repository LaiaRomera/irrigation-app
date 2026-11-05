"""Marcas sobre los datos diarios de madrugada: posibles riegos y días de instalación de la sonda.

Trabaja sobre las «fotos» de las 05:00 UTC (`training_table.dawn_snapshots`): una fila por unidad,
día y profundidad con `moisture_index` (0 a 100). Aquí solo se marca; excluir de la tabla de
entrenamiento es cosa de `training_table.py`.

- **Posible riego** (`possible_irrigation`): el índice sube entre dos madrugadas consecutivas más
  que un umbral y no llovió (o no se sabe) en ese intervalo.
- **Día de instalación** (`installation_days`): el tramo inicial de una unidad mientras su índice
  está por debajo de un umbral, antes de llegar por primera vez a un valor plausible. En ese
  tramo la sonda aún no está enterrada y el valor bajo no es sequedad real. Una unidad que
  empieza con valores plausibles no tiene instalación, aunque luego se seque.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

# Instalación: por debajo de este índice, la sonda se considera sin enterrar. Revisado con los
# datos reales del 4 de noviembre de 2026: las instalaciones dan 0 a 15 y el suelo enterrado
# arranca en 45 a 90.
INSTALL_THRESHOLD = 20.0
# Tope del tramo de instalación, en días desde el primer dato. Más allá no se marca: una serie
# que sigue baja pasado ese tiempo se deja a la lista manual.
INSTALL_MAX_DAYS = 30

# Posible riego: subida mínima del índice (puntos) y lluvia a partir de la cual la subida se
# atribuye a la lluvia (mm). Valores de partida; se ajustan en la tarea 2.5.
IRRIGATION_RISE_THRESHOLD = 2.0
IRRIGATION_RAIN_LIMIT_MM = 2.0

# Cómo juntar las profundidades de una unidad: «mean» (la media) o «any» (cualquier profundidad).
DEPTH_AGGREGATIONS = ("mean", "any")

# Lista manual de días de instalación por unidad: {unit_id: [(primer_dia, ultimo_dia), ...]}
# (ambos incluidos, formato «AAAA-MM-DD»). Se une a lo que marca la regla automática.
#
# - 5337 y 5515 (decidido el 5 de noviembre de 2026): sondas no enterradas durante semanas. Están
#   planas en 15 a 17 durante 67 y 49 días y saltan en un solo día a 87 y 91 (28-06-2026 y
#   28-09-2026). No es reposo del cultivo: la 5515 está plana en pleno verano. El tope de 30 días
#   de la regla automática no llega hasta el salto, de ahí la lista manual.
MANUAL_INSTALLATION: dict[int, list[tuple[str, str]]] = {
    5337: [("2026-04-22", "2026-06-27")],
    5515: [("2026-07-23", "2026-09-27")],
}

SUMMARY_COLUMNS = ["unit_id", "first_day", "last_day", "days_marked", "recovered"]


def _check_agg(depth_agg: str) -> None:
    if depth_agg not in DEPTH_AGGREGATIONS:
        raise ValueError(f"depth_agg debe ser uno de {DEPTH_AGGREGATIONS}, no {depth_agg!r}")


def possible_irrigation(
    snapshots: pd.DataFrame,
    rain: pd.DataFrame | None = None,
    rise_threshold: float = IRRIGATION_RISE_THRESHOLD,
    rain_limit_mm: float = IRRIGATION_RAIN_LIMIT_MM,
    depth_agg: str = "mean",
) -> pd.DataFrame:
    """Marca los días cuya madrugada llega con una subida del índice sin lluvia que la explique.

    La fila de `day` = d describe el intervalo de la madrugada de d−1 a la de d. `rise` es la
    subida: con `depth_agg="mean"` la media de las subidas de las profundidades que hay en los dos
    días; con `"any"`, la mayor de ellas. Un día sin foto el día anterior no tiene subida (nula) y
    no se marca.

    `rain` (opcional) trae `unit_id`, `day` y `rain_mm`, y `rain_mm` de la fila d es la lluvia del
    intervalo (se aproxima con la del día d−1 con antelación 0; quien llama la desplaza). Hay
    lluvia suficiente si `rain_mm >= rain_limit_mm`. Si `rain` es None no se mira la lluvia (caso
    más duro); si para un día no hay dato, la lluvia es desconocida y **no se usa para
    descartar** el riego (se marca igual).

    Devuelve `unit_id`, `day`, `rise`, `rain_mm` (nula si se desconoce) y `possible_irrigation`.
    """
    _check_agg(depth_agg)
    index = snapshots.set_index(["unit_id", "depth_cm", "day"])["moisture_index"]
    previous_key = pd.MultiIndex.from_arrays(
        [snapshots["unit_id"], snapshots["depth_cm"], snapshots["day"] - pd.Timedelta(days=1)]
    )
    rises = snapshots[["unit_id", "day"]].assign(
        rise=(snapshots["moisture_index"].to_numpy() - index.reindex(previous_key).to_numpy())
    )
    grouped = rises.groupby(["unit_id", "day"])["rise"]
    result = (grouped.mean() if depth_agg == "mean" else grouped.max()).reset_index()

    if rain is None:
        result["rain_mm"] = float("nan")
    else:
        result = result.merge(rain[["unit_id", "day", "rain_mm"]], on=["unit_id", "day"], how="left")
    rained = result["rain_mm"] >= rain_limit_mm  # nulo -> False: no descarta el riego
    result["possible_irrigation"] = (result["rise"] > rise_threshold) & ~rained
    return result[["unit_id", "day", "rise", "rain_mm", "possible_irrigation"]]


def _unit_daily_index(snapshots: pd.DataFrame, depth_agg: str) -> pd.DataFrame:
    """Un índice por unidad y día. «mean»: media de profundidades; «any»: la más baja."""
    grouped = snapshots.groupby(["unit_id", "day"])["moisture_index"]
    daily = grouped.mean() if depth_agg == "mean" else grouped.min()
    return daily.rename("index").reset_index().sort_values(["unit_id", "day"])


def installation_summary(
    snapshots: pd.DataFrame,
    threshold: float = INSTALL_THRESHOLD,
    max_days: int = INSTALL_MAX_DAYS,
    depth_agg: str = "any",
) -> pd.DataFrame:
    """Una fila por unidad con tramo de instalación automático: primer y último día marcados.

    El tramo empieza el primer día con datos de la unidad, solo si su índice ese día es menor que
    `threshold`, y llega hasta el día anterior al primero con índice >= `threshold`, con un tope
    de `max_days` días desde el primer dato. `recovered` dice si se llegó a un valor plausible
    dentro del tope (si es falso, la serie sigue baja y conviene revisarla a mano). Con
    `depth_agg="any"` basta una profundidad por debajo del umbral para seguir en instalación;
    con `"mean"`, que la media esté por debajo.
    """
    _check_agg(depth_agg)
    rows = []
    for unit_id, unit in _unit_daily_index(snapshots, depth_agg).groupby("unit_id"):
        if not unit["index"].iloc[0] < threshold:
            continue
        first_day = unit["day"].iloc[0]
        cap_day = first_day + pd.Timedelta(days=max_days - 1)
        plausible_at = (unit["index"] >= threshold).to_numpy().argmax() if (unit["index"] >= threshold).any() else len(unit)
        low_run = unit.iloc[:plausible_at]  # días con foto antes del primer valor plausible
        in_window = low_run[low_run["day"] <= cap_day]
        last_day = in_window["day"].iloc[-1]
        recovered = plausible_at < len(unit) and len(in_window) == len(low_run)
        rows.append((unit_id, first_day, last_day, (last_day - first_day).days + 1, recovered))
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def installation_days(
    snapshots: pd.DataFrame,
    threshold: float = INSTALL_THRESHOLD,
    max_days: int = INSTALL_MAX_DAYS,
    depth_agg: str = "any",
    manual: Mapping[int, Sequence[tuple[str, str]]] | None = None,
) -> pd.DataFrame:
    """Los días de instalación como pares (`unit_id`, `day`): regla automática más lista manual.

    Son días de calendario (incluye días del tramo sin foto). `manual` por defecto es
    `MANUAL_INSTALLATION` y solo cuenta para las unidades que aparecen en `snapshots`. Las fechas manuales se interpretan en la misma zona horaria que `day`.
    """
    manual = MANUAL_INSTALLATION if manual is None else manual
    ranges: list[tuple[int, pd.Timestamp, pd.Timestamp]] = [
        (row.unit_id, row.first_day, row.last_day)
        for row in installation_summary(snapshots, threshold, max_days, depth_agg).itertuples()
    ]
    tz = snapshots["day"].dt.tz
    present = set(snapshots["unit_id"].unique())
    for unit_id, spans in manual.items():
        if int(unit_id) not in present:  # una entrada manual de una unidad sin datos no marca nada
            continue
        for start, end in spans:
            ranges.append((int(unit_id), pd.Timestamp(start, tz=tz), pd.Timestamp(end, tz=tz)))

    pairs = {
        (unit_id, day)
        for unit_id, start, end in ranges
        for day in pd.date_range(start, end, freq="D")
    }
    result = pd.DataFrame(sorted(pairs), columns=["unit_id", "day"])
    if result.empty:
        result = pd.DataFrame({"unit_id": pd.Series(dtype="int64"), "day": pd.Series(dtype=snapshots["day"].dtype)})
    return result


def installation_mask(frame: pd.DataFrame, installation: pd.DataFrame, day_column: str = "day") -> pd.Series:
    """Verdadero en las filas de `frame` cuya unidad y día (`day_column`) son de instalación."""
    marked = pd.MultiIndex.from_frame(installation[["unit_id", "day"]])
    keys = pd.MultiIndex.from_arrays([frame["unit_id"], frame[day_column]])
    return pd.Series(keys.isin(marked), index=frame.index)
