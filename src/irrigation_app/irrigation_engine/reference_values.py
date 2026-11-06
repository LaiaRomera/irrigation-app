"""Tablas agronómicas FAO-56 para la parcela de referencia, con la fuente de cada cifra.

Fuente principal: Allen, R.G., Pereira, L.S., Raes, D. y Smith, M. (1998), «Crop evapotranspiration:
guidelines for computing crop water requirements», FAO Irrigation and Drainage Paper 56 (FAO-56).
Consultado en https://www.fao.org/4/x0490e/ el 5 de noviembre de 2026.

Cada valor es un `Reference` con su cifra, su fuente y un estado:

- `verified`: la cifra se leyó en la fuente citada (tabla y capítulo).
- `assumption`: una elección propia dentro de un intervalo de la fuente, o un valor que no está en
  ella. Se declara como supuesto y quien lo use debe mostrarlo como tal.

Una textura o un cultivo que no están en las tablas dan `UnknownReferenceError`; nunca se inventa
un valor.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

IRRIGATION_MANUAL = "FAO, Irrigation Water Management training manual (t7202e), Annex I"


def fmt_es(value: float, decimals: int | None = None) -> str:
    """Cifra para un texto al usuario, con coma decimal (el informe está en español).

    Con `decimals` fija esos decimales; sin él conserva los que trae el número (`0.3` -> `0,3`).
    Solo da formato a un texto: nunca se usa en campos numéricos ni en cálculos.
    """
    text = str(value) if decimals is None else f"{value:.{decimals}f}"
    return text.replace(".", ",")


class UnknownReferenceError(KeyError):
    """Cultivo, textura o tipo de riego que no están en las tablas."""

    def __str__(self) -> str:  # KeyError pone comillas al mensaje; aquí no
        return str(self.args[0])


@dataclass(frozen=True)
class Reference:
    """Una cifra con su unidad, su fuente y si se verificó o es un supuesto declarado."""

    value: float
    unit: str
    source: str
    status: Literal["verified", "assumption"]
    note: str = ""


def _normalize(name: str) -> str:
    return " ".join(str(name).strip().lower().split())


# --- Suelo: Tabla 19 (capítulo 7), intervalos típicos por textura ------------------------------
# La tabla da θCC y θPMP en m³/m³ y su diferencia; TAW (mm por metro de suelo) = 1000 × diferencia.
@dataclass(frozen=True)
class SoilTexture:
    name_es: str
    field_capacity: tuple[float, float]  # θCC, intervalo (m³/m³)
    wilting_point: tuple[float, float]  # θPMP, intervalo (m³/m³)
    available_range: tuple[float, float]  # θCC − θPMP, intervalo (m³/m³), columna de la tabla
    source: str


_TABLE_19 = "FAO-56, capítulo 7, Tabla 19 (características típicas del agua del suelo)"

SOIL_TEXTURES: dict[str, SoilTexture] = {
    "franco arcilloso limoso": SoilTexture(
        "franco arcilloso limoso", (0.30, 0.37), (0.17, 0.24), (0.13, 0.18), _TABLE_19 + ", fila «Silt clay loam»"
    ),
    "franco limoso": SoilTexture(
        "franco limoso", (0.22, 0.36), (0.09, 0.21), (0.13, 0.19), _TABLE_19 + ", fila «Silt loam»"
    ),
}
# Nombres alternativos (inglés de la tabla y variantes) -> clave de SOIL_TEXTURES
_TEXTURE_ALIASES = {
    "silty clay loam": "franco arcilloso limoso",
    "silt clay loam": "franco arcilloso limoso",
    "silt loam": "franco limoso",
}


def soil_texture(name: str) -> SoilTexture:
    """La fila de la Tabla 19 de una textura; error claro si no está en las tablas."""
    key = _normalize(name)
    key = _TEXTURE_ALIASES.get(key, key)
    try:
        return SOIL_TEXTURES[key]
    except KeyError:
        raise UnknownReferenceError(
            f"Textura desconocida: {name!r}. Hay valores para: {sorted(SOIL_TEXTURES)}. "
            "No se inventa ningún valor: hay que añadir la fila de la Tabla 19 de FAO-56."
        ) from None


def total_available_water_per_m(name: str) -> Reference:
    """TAW en mm por metro de suelo = 1000 × (θCC − θPMP). Se toma el punto medio del intervalo.

    El intervalo de la Tabla 19 está verificado; elegir el punto medio es un supuesto declarado
    (en la 1168 no hay análisis de suelo). El resto del intervalo sirve para la sensibilidad.
    """
    texture = soil_texture(name)
    low, high = texture.available_range
    return Reference(
        value=round(1000 * (low + high) / 2, 1),
        unit="mm/m",
        source=texture.source,
        status="assumption",
        note=f"Intervalo de la tabla {low * 1000:.0f} a {high * 1000:.0f} mm/m; se usa el punto medio.",
    )


def available_water_range_per_m(name: str) -> tuple[float, float]:
    """Intervalo de TAW de la Tabla 19, en mm/m (verificado en la fuente)."""
    low, high = soil_texture(name).available_range
    return round(1000 * low, 1), round(1000 * high, 1)


def field_capacity_and_wilting_point(name: str) -> tuple[Reference, Reference]:
    """θCC y θPMP (m³/m³) como punto medio de sus intervalos de la Tabla 19 (supuesto declarado)."""
    texture = soil_texture(name)

    def mid(rng: tuple[float, float], label: str) -> Reference:
        return Reference(
            round(sum(rng) / 2, 3), "m3/m3", texture.source, "assumption",
            f"{label}: intervalo de la tabla {fmt_es(rng[0])} a {fmt_es(rng[1])}; punto medio.",
        )

    return mid(texture.field_capacity, "θCC"), mid(texture.wilting_point, "θPMP")


# --- Cultivo: Tablas 11, 12 (capítulo 6) y 22 (capítulo 8) -------------------------------------
@dataclass(frozen=True)
class CropStage:
    name: str  # ini, dev, mid, late
    days: int


@dataclass(frozen=True)
class Crop:
    name_es: str
    root_depth_m: tuple[float, float]  # Zr, Tabla 22
    depletion_fraction: float  # p para ETc ≈ 5 mm/día, Tabla 22
    kc: dict[str, tuple[float, float, float]]  # caso de cobertura -> (Kc ini, Kc mid, Kc end), Tabla 12
    stage_days: tuple[CropStage, ...]  # Tabla 11 (frutales de hoja caduca, no específica del manzano)
    sources: dict[str, str]


CROPS: dict[str, Crop] = {
    "manzano": Crop(
        name_es="manzano",
        root_depth_m=(1.0, 2.0),
        depletion_fraction=0.50,
        kc={
            "no_cover_killing_frost": (0.45, 0.95, 0.70),
            "no_cover_no_frost": (0.60, 0.95, 0.75),
            "active_cover_killing_frost": (0.50, 1.20, 0.95),
            "active_cover_no_frost": (0.80, 1.20, 0.85),
        },
        # Tabla 11, «Deciduous Orchard», latitudes altas, plantación en marzo: 20+70+90+30 = 210 días.
        stage_days=(CropStage("ini", 20), CropStage("dev", 70), CropStage("mid", 90), CropStage("late", 30)),
        sources={
            "root_depth_m": "FAO-56, capítulo 8, Tabla 22, fila «Apples, Cherries, Pears»: Zr 1,0 a 2,0 m "
            "(nota 1: los valores menores para programar el riego)",
            "depletion_fraction": "FAO-56, capítulo 8, Tabla 22, fila «Apples, Cherries, Pears»: p = 0,50 "
            "para ETc ≈ 5 mm/día; ajuste de la nota 2: p = p(Tabla 22) + 0,04 (5 − ETc), entre 0,1 y 0,8",
            "kc": "FAO-56, capítulo 6, Tabla 12, fila «Apples, Cherries, Pears» (nota 19): cuatro casos "
            "según cobertura del suelo y heladas; Kc fin = valor antes de la caída de la hoja (nota 18)",
            "stage_days": "FAO-56, capítulo 6, Tabla 11, «Deciduous Orchard»: 20/70/90/30 días (latitudes altas, "
            "plantación en marzo). No es específica del manzano y la propia tabla se declara «solo una guía»",
        },
    ),
}
_CROP_ALIASES = {"apple": "manzano", "apples": "manzano", "manzana": "manzano"}


def crop(name: str) -> Crop:
    """Los datos FAO-56 de un cultivo; error claro si no está en las tablas."""
    key = _normalize(name)
    key = _CROP_ALIASES.get(key, key)
    try:
        return CROPS[key]
    except KeyError:
        raise UnknownReferenceError(
            f"Cultivo desconocido: {name!r}. Hay valores para: {sorted(CROPS)}. "
            "No se inventa ningún valor: hay que añadir sus filas de las Tablas 11, 12 y 22 de FAO-56."
        ) from None


# Cubierta vegetal entre las filas (valores que acepta el campo `ground_cover` de la parcela).
GROUND_COVERS = ("active_cover", "no_cover")
# Se asume que hay heladas que matan la hoja (Tabla 12); no se pregunta por ellas.
ASSUMED_FROST = "killing_frost"
# Caso de Kc cuando la parcela no dice nada de la cubierta: sin cubierta y con helada (el más prudente,
# Kc de pleno verano más bajo). En la 1168 no se usa: su `ground_cover` es el supuesto de Laia.
DEFAULT_KC_CASE = "no_cover_killing_frost"


def kc_case(ground_cover: str | None, frost: str = ASSUMED_FROST) -> str:
    """Caso de la Tabla 12 (`active_cover_killing_frost`, ...) para la cubierta de la parcela.

    `ground_cover` es `active_cover` o `no_cover`; `None` (desconocida) da `DEFAULT_KC_CASE`.
    """
    if ground_cover is None:
        return DEFAULT_KC_CASE
    cover = _normalize(ground_cover)
    if cover not in GROUND_COVERS:
        raise UnknownReferenceError(f"Cubierta desconocida: {ground_cover!r}. Hay: {list(GROUND_COVERS)}.")
    return f"{cover}_{frost}"


def ground_cover_limitation(crop_name: str, ground_cover: str) -> str:
    """Texto de la limitación `assumed_ground_cover`: qué Kc se usa y cuánto cambiaría con la otra opción."""
    used = kc_values(crop_name, kc_case(ground_cover))
    other = kc_values(crop_name, kc_case("no_cover" if _normalize(ground_cover) == "active_cover" else "active_cover"))
    label = "con cubierta vegetal activa" if _normalize(ground_cover) == "active_cover" else "sin cubierta"
    other_label = "sin cubierta" if label.startswith("con") else "con cubierta vegetal activa"
    return (
        f"Cubierta vegetal entre las filas sin confirmar: se usa el caso «{label}» (Kc {fmt_es(used[0], 2)} / {fmt_es(used[1], 2)} / "
        f"{fmt_es(used[2], 2)}, Tabla 12). {other_label.capitalize()} el Kc de pleno verano sería {fmt_es(other[1], 2)} "
        f"en vez de {fmt_es(used[1], 2)} y la dosis cambiaría en esa proporción ({other[1] / used[1] - 1:+.0%})."
    )


def root_depth_for_scheduling(crop_name: str) -> Reference:
    """Zr para programar el riego: el valor menor de la Tabla 22 (nota 1)."""
    c = crop(crop_name)
    return Reference(
        c.root_depth_m[0], "m", c.sources["root_depth_m"], "verified",
        f"Intervalo completo {fmt_es(c.root_depth_m[0])} a {fmt_es(c.root_depth_m[1])} m; se toma el menor (nota 1).",
    )


def depletion_fraction(crop_name: str, etc_mm_day: float | None = None) -> Reference:
    """p de la Tabla 22; con `etc_mm_day` se aplica el ajuste de la nota 2 (límites 0,1 y 0,8).

    No se aplica la reducción de 5 a 10 % para suelos finos del texto del capítulo 8: queda dicho
    en la nota.
    """
    c = crop(crop_name)
    p = c.depletion_fraction
    note = "Valor de la tabla para ETc ≈ 5 mm/día."
    if etc_mm_day is not None:
        p = min(0.8, max(0.1, p + 0.04 * (5 - etc_mm_day)))
        note = f"Ajustado a ETc = {fmt_es(etc_mm_day)} mm/día con la nota 2 de la Tabla 22."
    note += " No se reduce por suelo fino (el texto del capítulo 8 sugiere 5 a 10 %)."
    return Reference(round(p, 3), "fracción", c.sources["depletion_fraction"], "verified", note)


def kc_values(crop_name: str, case: str = DEFAULT_KC_CASE) -> tuple[float, float, float]:
    """(Kc ini, Kc mid, Kc end) de la Tabla 12 para un caso de cobertura y heladas."""
    c = crop(crop_name)
    try:
        return c.kc[case]
    except KeyError:
        raise UnknownReferenceError(f"Caso de Kc desconocido: {case!r}. Hay: {sorted(c.kc)}.") from None


def stage_at(crop_name: str, day_of_cycle: int) -> str | None:
    """Etapa (`ini`, `dev`, `mid`, `late`) del día de ciclo (0 = inicio) según la Tabla 11.

    Devuelve None fuera del ciclo (hoja caída o reposo): la Tabla 12 no da Kc ahí.
    """
    if day_of_cycle < 0:
        return None
    elapsed = 0
    for stage in crop(crop_name).stage_days:
        elapsed += stage.days
        if day_of_cycle < elapsed:
            return stage.name
    return None


# --- Calendario del ciclo y Kc por día (Tablas 11 y 12, capítulo 6) -----------------------------
# Inicio del ciclo cuando la parcela no lo dice (limitación `assumed_crop_stage`): 1 de marzo (mes, día). La Tabla 11
# da la fila «Deciduous Orchard» (20/70/90/30 días) para plantación en marzo en latitudes altas; el día 1 es una
# elección propia dentro de ese mes, aprobada por Laia el 5 de noviembre de 2026. El ciclo dura 210 días: del 1 de marzo al
# 26 de septiembre; fuera de él (hoja caída o reposo) la Tabla 12 no da Kc.
ASSUMED_CYCLE_START = (3, 1)
ASSUMED_CYCLE_START_SOURCE = (
    "FAO-56, capítulo 6, Tabla 11, «Deciduous Orchard» (plantación en marzo, latitudes altas); el día 1 de marzo "
    "es un supuesto declarado"
)


def cycle_start_in_year(month: int, day: int, year: int) -> dt.date:
    """Inicio del ciclo en un año dado (el 29 de febrero pasa al 28 en los años no bisiestos)."""
    try:
        return dt.date(year, month, day)
    except ValueError:
        return dt.date(year, month, day - 1)


def day_of_cycle(date: dt.date, start: tuple[int, int] = ASSUMED_CYCLE_START) -> int:
    """Día del ciclo (0 = inicio) de una fecha, con el inicio del mismo año. Negativo antes del inicio."""
    return (date - cycle_start_in_year(start[0], start[1], date.year)).days


def kc_at(crop_name: str, case: str, day_in_cycle: int) -> tuple[str, float] | None:
    """(etapa, Kc) del día de ciclo con la curva de FAO-56: Kc ini constante, lineal hasta Kc mid al final de
    desarrollo, Kc mid constante y lineal hasta Kc fin al final de la etapa tardía (capítulo 6, Figura 25).

    Devuelve None fuera del ciclo (la Tabla 12 no da Kc en reposo)."""
    stage = stage_at(crop_name, day_in_cycle)
    if stage is None:
        return None
    kc_ini, kc_mid, kc_end = kc_values(crop_name, case)
    stages = crop(crop_name).stage_days
    start_of = {}
    elapsed = 0
    for st in stages:
        start_of[st.name] = (elapsed, st.days)
        elapsed += st.days
    first, length = start_of[stage]
    progress = (day_in_cycle - first + 1) / length  # 0 a 1, al final del día
    if stage == "ini":
        kc = kc_ini
    elif stage == "dev":
        kc = kc_ini + (kc_mid - kc_ini) * progress
    elif stage == "mid":
        kc = kc_mid
    else:
        kc = kc_mid + (kc_end - kc_mid) * progress
    return stage, round(kc, 3)


# --- Riego localizado: Tabla 20 y ejemplo 34 (capítulo 7) --------------------------------------
WETTED_FRACTION_TRICKLE = Reference(
    0.35, "fracción", "FAO-56, capítulo 7, Tabla 20, fila «Trickle irrigation»: fw 0,3 a 0,4",
    "assumption", "Punto medio del intervalo 0,3 a 0,4 (supuesto declarado).",
)


def drip_exposed_wetted_fraction(fc: float, fw: float | None = None) -> Reference:
    """few bajo goteo: fw × [1 − (2/3)·fc] (FAO-56, capítulo 7, texto de la Tabla 20 y ejemplo 34).

    `fc` es la fracción del suelo cubierta por la copa (0 a 1). Con goteo bajo la copa, FAO-56 dice
    que fw puede reducirse a la mitad o un tercio y propone multiplicar por [1 − (2/3)fc]. Solo
    afecta a la evaporación del suelo (Ke) en el método de doble Kc. FAO-56 no da una reducción de
    Kc propia del goteo en árboles, así que el goteo de una línea (franja mojada, no todo el suelo)
    es una limitación que se declara en el resultado.
    """
    if not 0 <= fc <= 1:
        raise ValueError(f"fc debe estar entre 0 y 1, no {fc}")
    base = WETTED_FRACTION_TRICKLE.value if fw is None else fw
    return Reference(
        round(base * (1 - 2 / 3 * fc), 4), "fracción",
        "FAO-56, capítulo 7, Tabla 20 y ejemplo 34: few = fw·[1 − (2/3)fc] para goteo", "assumption",
        f"fw = {fmt_es(base)}, fc = {fmt_es(fc)}.",
    )



# --- Dosis del riego localizado: reposición de la ETc del intervalo ---------------------------------
DRIP_REPLENISHMENT_INTERVAL_DAYS = Reference(
    3, "días",
    "Supuesto propio, sin cifra en FAO-56. FAO-56 (capítulo 8, «Irrigation scheduling») indica que el riego de alta "
    "frecuencia (goteo) repone la ETc de cada intervalo y mantiene el suelo cerca de capacidad de campo; el número de "
    "días no está tabulado.",
    "assumption",
    "Intervalo de reposición del goteo: cada evento devuelve lo que el cultivo gasta en 3 días. Cambiable con "
    "`EngineInput.replenishment_interval_days`.",
)
LOCALIZED_IRRIGATION_TYPES = ("drip_one_line",)


def is_localized(irrigation_type: str | None) -> bool:
    """True si el riego es localizado (hoy, goteo de una línea): moja solo una franja del suelo."""
    if irrigation_type is None:
        return False
    key = _normalize(irrigation_type)
    return _IRRIGATION_ALIASES.get(key, key) in LOCALIZED_IRRIGATION_TYPES


def readily_available_water_mm(p: float, taw_mm: float) -> float:
    """RAW = p × TAW (FAO-56, ecuación 83): el agua que el cultivo puede gastar sin estrés; es el tope de un evento."""
    return p * taw_mm


# --- Eficiencia de aplicación: no está en FAO-56, sale del manual de gestión del agua de la FAO -
_EA_SOURCE = IRRIGATION_MANUAL + ", Tabla 8 (valores indicativos de la eficiencia de aplicación en parcela)"
IRRIGATION_EFFICIENCY: dict[str, Reference] = {
    "drip_one_line": Reference(0.90, "fracción", _EA_SOURCE + ", «Drip irrigation» 90 %", "verified"),
    "sprinkler": Reference(0.75, "fracción", _EA_SOURCE + ", «Sprinkler irrigation» 75 %", "verified"),
    "surface": Reference(0.60, "fracción", _EA_SOURCE + ", «Surface irrigation» 60 %", "verified"),
}
# Si se desconoce el tipo de riego (limitación `assumed_irrigation_efficiency`): 0,75, el valor
# intermedio de la tabla (aspersión). Elección mía, pendiente del visto bueno de Laia.
GENERIC_IRRIGATION_EFFICIENCY = Reference(
    0.75, "fracción", _EA_SOURCE + ", valor intermedio (aspersión)", "assumption",
    "Eficiencia genérica cuando no se conoce el tipo de riego.",
)
_IRRIGATION_ALIASES = {"goteo": "drip_one_line", "goteo de una línea": "drip_one_line", "goteo de una linea": "drip_one_line"}


def irrigation_efficiency(irrigation_type: str | None) -> Reference:
    """Ea del tipo de riego; sin tipo (None), la eficiencia genérica declarada como supuesto."""
    if irrigation_type is None:
        return GENERIC_IRRIGATION_EFFICIENCY
    key = _normalize(irrigation_type)
    key = _IRRIGATION_ALIASES.get(key, key)
    try:
        return IRRIGATION_EFFICIENCY[key]
    except KeyError:
        raise UnknownReferenceError(
            f"Tipo de riego desconocido: {irrigation_type!r}. Hay valores para: {sorted(IRRIGATION_EFFICIENCY)}."
        ) from None
