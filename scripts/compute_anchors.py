"""Calcula y MUESTRA los anclajes de la escala del sensor de una unidad (por defecto la 1168).

Dos cosas, para contrastarlas:
1. Las líneas de Agrodevices (en frecuencia) convertidas al índice de 0 a 100 de la unidad:
   índice = (smm_max − f) / (smm_max − smm_min) × 100.
2. Los percentiles de la media de las profundidades de la zona radicular (las marcadas como incluidas
   en la media de la unidad: 15, 25 y 35 cm en la 1168) sobre las fotos de las 05:00 UTC de
   data/silver/snapshots.parquet, SIN los días de instalación (unión de la regla automática, la
   lista manual y los días con lecturas SMMAdmin, como en prepare_training_table.py).

Los anclajes son globales (uno superior y uno inferior para esa media). El script no escribe nada:
los valores se copian a `REFERENCE_PLOT` solo tras revisarlos con Laia.

Uso (desde la raíz del proyecto):
    .venv/Scripts/python scripts/compute_anchors.py [--unit-id 1168]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from irrigation_app.data_layers import DAILY_PATH, SNAPSHOTS_PATH, UNITS_PATH  # noqa: E402
from irrigation_app.irrigation_marks import admin_days, installation_days, installation_mask  # noqa: E402
from irrigation_app.reference_plot import REFERENCE_PLOT  # noqa: E402

# Líneas de Agrodevices de la 1168 (frecuencia), dadas por Laia el 5 de noviembre de 2026
AGRODEVICES_LINES_1168 = {
    "saturation": 7889,
    "upper_optimal_recharge": 8234,
    "upper_hard_to_use_reserve": 8570,
    "permanent_wilting_point": 8834,
}
PERCENTILES = (1, 5, 10, 50, 90, 95, 99)


def frequency_to_index(frequency: float, smm_min: float, smm_max: float) -> float:
    """Frecuencia de la sonda -> índice de 0 a 100 con el rango de la unidad (sin recortar)."""
    if not smm_max > smm_min:
        raise ValueError(f"Rango de la unidad no válido: smm_min={smm_min}, smm_max={smm_max}")
    return (smm_max - frequency) / (smm_max - smm_min) * 100


def agrodevices_anchors(lines: dict[str, float], smm_min: float, smm_max: float) -> dict[str, float]:
    """Todas las líneas de Agrodevices convertidas al índice."""
    return {name: frequency_to_index(f, smm_min, smm_max) for name, f in lines.items()}


def root_zone_mean(snapshots: pd.DataFrame, unit_id: int, depths_cm: list[int], installation: pd.DataFrame) -> pd.Series:
    """Media diaria (05:00 UTC) de las profundidades dadas, sin los días de instalación.

    Un día solo cuenta si están todas las profundidades; una media con menos capas no es comparable.
    """
    unit = snapshots[(snapshots["unit_id"] == unit_id) & snapshots["depth_cm"].isin(depths_cm)]
    unit = unit[~installation_mask(unit, installation)]
    grouped = unit.groupby("day")["moisture_index"]
    mean = grouped.mean()
    return mean[grouped.count() == len(depths_cm)].sort_index()


def percentiles_of(series: pd.Series, levels: tuple[int, ...] = PERCENTILES) -> dict[int, float]:
    """Percentiles de una serie (sin nulos); error claro si está vacía."""
    values = series.dropna().to_numpy(dtype=float)
    if values.size == 0:
        raise ValueError("No hay datos para calcular percentiles")
    return {p: float(np.percentile(values, p)) for p in levels}


def share_below(series: pd.Series, threshold: float) -> float:
    """Fracción de días con la media por debajo de un valor."""
    values = series.dropna()
    return float((values < threshold).mean()) if len(values) else float("nan")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--unit-id", type=int, default=1168)
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS_PATH)
    parser.add_argument("--units", type=Path, default=UNITS_PATH)
    parser.add_argument("--daily", type=Path, default=DAILY_PATH)
    args = parser.parse_args()
    for path in (args.snapshots, args.units, args.daily):
        if not path.exists():
            sys.exit(f"No existe el archivo {path}")
    if args.unit_id != 1168:
        sys.exit("Solo la 1168 tiene las líneas de Agrodevices anotadas; para otra unidad hay que calcularlas de los datos.")

    units = pd.read_parquet(args.units)
    unit = units[units["unit_id"] == args.unit_id]
    if unit.empty:
        sys.exit(f"La unidad {args.unit_id} no está en {args.units}")
    unit = unit.iloc[0]
    smm_min, smm_max = float(unit["smm_min"]), float(unit["smm_max"])
    depths = [
        int(unit[f"depth_cm_{i}"])
        for i in range(1, 6)
        if pd.notna(unit[f"depth_cm_{i}"]) and bool(unit[f"in_average_{i}"])
    ]

    snapshots = pd.read_parquet(args.snapshots)
    admin = admin_days(pd.read_parquet(args.daily, columns=["unit_id", "day", "n_admin"]))
    installation = installation_days(snapshots, admin=admin)
    unit_installation = installation[installation["unit_id"] == args.unit_id]
    mean = root_zone_mean(snapshots, args.unit_id, depths, installation)

    print(f"Unidad {args.unit_id} ({unit['name']}), rango de la sonda {smm_min:.0f} a {smm_max:.0f}")
    print(f"Profundidades en la media: {depths} cm (las marcadas como incluidas en la media de la unidad)")
    print(f"Periodo: {mean.index.min().date()} a {mean.index.max().date()}, {len(mean)} días con las {len(depths)} capas")
    print(f"Días de instalación excluidos en esta unidad: {len(unit_installation)} (unión de las tres reglas)")
    print("Método: fotos de las 05:00 UTC, media de las capas por día, percentiles empíricos\n")

    lines = agrodevices_anchors(AGRODEVICES_LINES_1168, smm_min, smm_max)
    print("1) Líneas de Agrodevices (fuente `agrodevices`, calibradas a mano según Laia)")
    print(f"   {'línea':<28}{'frecuencia':>11}{'índice':>9}{'% días por debajo':>20}")
    for name, index in lines.items():
        print(f"   {name:<28}{AGRODEVICES_LINES_1168[name]:>11}{index:>9.1f}{share_below(mean, index):>20.1%}")

    print("\n2) Percentiles de la media de las capas (sin días de instalación)")
    print("   " + "  ".join(f"p{p}={v:.1f}" for p, v in percentiles_of(mean).items()))
    print(f"   mínimo={mean.min():.1f}  máximo={mean.max():.1f}")

    print("\n3) Contraste de los anclajes globales")
    pct = percentiles_of(mean)
    top, bottom = lines["saturation"], lines["permanent_wilting_point"]
    print(f"   Anclaje superior (saturación):     Agrodevices {top:.1f}  | máximo de los datos {mean.max():.1f}, p99 {pct[99]:.1f}")
    print(f"   Anclaje inferior (marchitez):      Agrodevices {bottom:.1f}  | p1 {pct[1]:.1f}, p5 {pct[5]:.1f}, p10 {pct[10]:.1f}")
    print(f"   Agua útil de esa escala: {top - bottom:.1f} puntos del índice")
    for label, anchor in (("superior", REFERENCE_PLOT.anchors.upper), ("inferior", REFERENCE_PLOT.anchors.lower)):
        if anchor.review == "confirmed":
            print(f"   Estado del anclaje {label}: CONFIRMADO por {anchor.reviewed_by} el {anchor.reviewed_on:%d-%m-%Y} en REFERENCE_PLOT.")
        else:
            print(f"   Estado del anclaje {label}: PENDIENTE de visto bueno de Laia; solo entonces se confirma en REFERENCE_PLOT.")


if __name__ == "__main__":
    main()
