"""Cálculo del motor de riego FAO-56: funciones puras, sin red ni base de datos.

Pasos (ver `docs/arquitectura.md`, sección «Motor de riego»):

1. Humedad de la zona radicular: media de las sondas de `plot.average_depths_cm` (la de 55 cm no entra).
2. Fracción de agua útil gastada con los anclajes globales: D = (anclaje superior − humedad) / (superior − inferior).
3. ETc = Kc × ET₀ (la ET₀ viene de Open-Meteo; no se calcula) y p ajustada a esa ETc (Tabla 22, nota 2).
4. Primer día en que D > p decide *cuándo* regar. Dosis (mm):
   - riego localizado (goteo): reposición de la ETc de los días del intervalo de reposición, con dos topes: el
     agotamiento actual D × TAW × Zr y el agua fácilmente aprovechable RAW = p × TAW × Zr. Son mm sobre la franja mojada.
   - resto de casos: lo que falta para volver a capacidad de campo = D × TAW × Zr.
   La bruta = neta / eficiencia.
5. Análisis de sensibilidad: se repite con los anclajes desplazados ±10 % de su rango.

La meteorología prevista no se suma a la humedad predicha: el modelo ya la tiene dentro. Aquí la ET₀ solo da la ETc
(que ajusta p y se muestra) y la lluvia solo sirve para aplazar un riego. Mismas entradas, mismo resultado.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

from irrigation_app.irrigation_engine import reference_values as rv
from irrigation_app.irrigation_engine.inputs import PLAN_DAYS, EngineInput, SensorPrediction, WeatherDay
from irrigation_app.irrigation_engine.result import (
    DayPlan,
    DoseLimit,
    EngineResult,
    Factor,
    Limitation,
    Sensitivity,
    SensitivityCase,
)
from irrigation_app.reference_plot import ReferencePlot

SENSITIVITY_FRACTION = 0.10  # los anclajes se desplazan un 10 % de su rango
# Lluvia prevista (mm en el día) a partir de la cual se aplaza un riego. Umbral propio, sin fuente FAO-56:
# aprobado por Laia el 5 de noviembre de 2026.
RAIN_POSTPONE_MM = 5.0


# --- Funciones puras pequeñas -------------------------------------------------------------------
def root_zone_mean(
    predictions: Sequence[SensorPrediction], depths_cm: Sequence[int], horizon_days: int
) -> tuple[float | None, tuple[int, ...], tuple[int, ...]]:
    """Media de la humedad predicha de las sondas de la zona radicular para un horizonte.

    Devuelve (media o None, profundidades usadas, profundidades sin predicción). Una sonda con calidad
    `insufficient_data` o sin valor cuenta como ausente. Las sondas que no están en `depths_cm` no intervienen.
    """
    by_depth = {
        p.depth_cm: p.moisture_pred
        for p in predictions
        if p.horizon_days == horizon_days and p.quality != "insufficient_data" and p.moisture_pred is not None
    }
    used = tuple(sorted(d for d in depths_cm if d in by_depth))
    missing = tuple(sorted(d for d in depths_cm if d not in by_depth))
    if not used:
        return None, used, missing
    return sum(by_depth[d] for d in used) / len(used), used, missing


def depletion_fraction(moisture: float, upper: float, lower: float) -> float:
    """Fracción de agua útil gastada, sin recortar: (superior − humedad) / (superior − inferior)."""
    if not upper > lower:
        raise ValueError("el anclaje superior debe ser mayor que el inferior")
    return (upper - moisture) / (upper - lower)


def clamp_unit(value: float) -> float:
    return min(1.0, max(0.0, value))


def etc_mm(kc: float, eto_mm: float) -> float:
    """ETc = Kc × ET₀ (FAO-56, ecuación 58)."""
    return kc * eto_mm


def net_dose_mm(depletion: float, taw_mm_per_m: float, root_depth_m: float) -> float:
    """Dosis neta: lo que falta para volver a capacidad de campo = D × TAW × Zr (mm)."""
    return depletion * taw_mm_per_m * root_depth_m


def gross_dose_mm(net_mm: float, efficiency: float) -> float:
    """Dosis bruta = neta / eficiencia de aplicación (0 a 1)."""
    if not 0 < efficiency <= 1:
        raise ValueError(f"eficiencia fuera de (0, 1]: {efficiency}")
    return net_mm / efficiency


def replenishment_dose_mm(
    etc_window: Sequence[float | None], interval_days: int, depletion_mm: float, raw_mm: float
) -> tuple[float, float | None, DoseLimit]:
    """Dosis neta del goteo: reposición de la ETc del intervalo, sin pasar del agotamiento ni de RAW.

    `etc_window` es la ETc de los días del intervalo que caen dentro del plan (puede ser más corta que el
    intervalo, o llevar `None`). Con los días conocidos se toma su media y se multiplica por el intervalo.
    Sin ninguna ETc no hay reposición que calcular: la dosis es el menor de agotamiento y RAW (`no_etc`).
    Devuelve (dosis neta, ETc del intervalo o None, límite que fijó la dosis). En un empate manda la reposición.
    """
    known = [e for e in etc_window if e is not None]
    if not known:
        net = min(depletion_mm, raw_mm)
        return net, None, "no_etc"
    etc_interval = sum(known) / len(known) * interval_days
    net = min(etc_interval, depletion_mm, raw_mm)
    if net == etc_interval:
        limit: DoseLimit = "replenishment"
    elif net == depletion_mm:
        limit = "depletion"
    else:
        limit = "raw_cap"
    return net, etc_interval, limit


def volume_m3(gross_mm: float, area_m2: float | None) -> float | None:
    """Volumen = lámina bruta (mm) × superficie (m²) / 1000. Sin superficie, None (nunca una cifra supuesta)."""
    if area_m2 is None:
        return None
    return gross_mm * area_m2 / 1000


# --- Contexto de cada día (todo lo que no depende de los anclajes) --------------------------------
@dataclass(frozen=True)
class _DayContext:
    day: int
    date: dt.date
    moisture_mean: float | None
    used: tuple[int, ...]
    missing: tuple[int, ...]
    in_season: bool
    stage: str | None
    kc: float | None
    eto: float | None
    etc: float | None
    rain: float | None
    p: float | None


def _kc_and_stage(crop_name: str, case: str, date: dt.date, cycle_start: tuple[int, int]) -> tuple[str, float] | None:
    return rv.kc_at(crop_name, case, rv.day_of_cycle(date, cycle_start))


def _build_contexts(
    inp: EngineInput, crop_name: str, kc_case: str, cycle_start: tuple[int, int]
) -> list[_DayContext]:
    weather: dict[dt.date, WeatherDay] = {w.date: w for w in inp.weather}
    depths = inp.plot.average_depths_cm
    contexts = []
    for k in range(1, PLAN_DAYS + 1):
        date = inp.origin_date + dt.timedelta(days=k)
        mean, used, missing = root_zone_mean(inp.predictions, depths, k)
        w = weather.get(date)
        eto = w.eto_mm if w else None
        rain = w.rain_mm if w else None
        kc_stage = _kc_and_stage(crop_name, kc_case, date, cycle_start)
        if kc_stage is None:
            contexts.append(_DayContext(k, date, mean, used, missing, False, None, None, eto, None, rain, None))
            continue
        stage, kc = kc_stage
        kc = round(kc, 2)
        etc = round(etc_mm(kc, eto), 2) if eto is not None else None
        p = rv.depletion_fraction(crop_name, etc).value  # sin ETc, el valor de la tabla (ETc ≈ 5 mm/día)
        contexts.append(_DayContext(k, date, mean, used, missing, True, stage, kc, eto, etc, rain, p))
    return contexts


# --- Plan de 7 días -----------------------------------------------------------------------------
def evaluate_plan(
    contexts: Sequence[_DayContext],
    upper: float,
    lower: float,
    taw_per_m: float,
    root_depth_m: float,
    efficiency: float,
    area_m2: float | None,
    localized: bool = False,
    interval_days: int = 1,
) -> list[DayPlan]:
    """Decisión de cada día con unos anclajes dados. Solo el primer cruce de p recibe `irrigate` y su dosis.

    Con `localized` la dosis es la reposición de la ETc de `interval_days` días desde el día de riego (con los
    topes de agotamiento y RAW); si no, vuelve toda la zona radicular a capacidad de campo.

    Orden de las reglas por día: fuera de campaña, sin datos, ya regado antes, bajo el umbral, cruce con lluvia
    prevista (`postpone`) y cruce sin lluvia (`irrigate`).
    """
    plan: list[DayPlan] = []
    irrigated = False
    for i, c in enumerate(contexts):
        common = dict(
            day=c.day, date=c.date, moisture_mean=None if c.moisture_mean is None else round(c.moisture_mean, 2),
            probes_used_cm=c.used, probes_missing_cm=c.missing, stage=c.stage, kc=c.kc, eto_mm=c.eto,
            etc_mm=c.etc, rain_mm=c.rain, p=c.p,
        )
        depletion = clamped = None
        if c.moisture_mean is not None:
            raw = depletion_fraction(c.moisture_mean, upper, lower)
            depletion = clamp_unit(raw)
            clamped = raw != depletion
            common.update(depletion=round(depletion, 3), depletion_clamped=clamped)
        if not c.in_season:
            plan.append(DayPlan(decision="do_not_irrigate", reasons=("out_of_season",), **common))
        elif depletion is None:
            plan.append(DayPlan(decision="insufficient_data", reasons=("no_probe_prediction",), **common))
        elif irrigated:
            plan.append(DayPlan(decision="do_not_irrigate", reasons=("after_irrigation",), **common))
        elif depletion <= c.p:
            plan.append(DayPlan(decision="do_not_irrigate", reasons=("below_threshold",), **common))
        elif c.rain is not None and c.rain >= RAIN_POSTPONE_MM:
            plan.append(DayPlan(decision="postpone", reasons=("threshold_crossed", "rain_forecast"), **common))
        else:
            depletion_mm = net_dose_mm(depletion, taw_per_m, root_depth_m)
            raw_mm = rv.readily_available_water_mm(c.p, taw_per_m * root_depth_m)
            if localized:
                window = [w.etc for w in contexts[i : i + interval_days]]
                net, etc_interval, limit = replenishment_dose_mm(window, interval_days, depletion_mm, raw_mm)
            else:
                net, etc_interval, limit = depletion_mm, None, "field_capacity"
            gross = gross_dose_mm(net, efficiency)
            vol = volume_m3(gross, area_m2)
            irrigated = True
            plan.append(
                DayPlan(
                    decision="irrigate", reasons=("threshold_crossed",), net_dose_mm=round(net, 1),
                    gross_dose_mm=round(gross, 1), volume_m3=None if vol is None else round(vol, 1),
                    depletion_mm=round(depletion_mm, 1), raw_cap_mm=round(raw_mm, 1) if localized else None,
                    etc_interval_mm=None if etc_interval is None else round(etc_interval, 1), dose_limit=limit,
                    **common,
                )
            )
    return plan


def first_irrigation(plan: Sequence[DayPlan]) -> DayPlan | None:
    return next((d for d in plan if d.decision == "irrigate"), None)


def _sensitivity_case(label: str, plan: Sequence[DayPlan], upper: float, lower: float) -> SensitivityCase:
    irr = first_irrigation(plan)
    return SensitivityCase(
        label=label, upper_anchor=round(upper, 2), lower_anchor=round(lower, 2),
        irrigation_day=irr.day if irr else None,
        net_dose_mm=irr.net_dose_mm if irr else None, gross_dose_mm=irr.gross_dose_mm if irr else None,
    )


def sensitivity_analysis(
    contexts: Sequence[_DayContext], upper: float, lower: float, taw_per_m: float, root_depth_m: float,
    efficiency: float, area_m2: float | None, base_plan: Sequence[DayPlan], localized: bool = False,
    interval_days: int = 1,
) -> Sensitivity:
    """Repite el plan con los dos anclajes desplazados ±10 % de su rango (las cuatro esquinas)."""
    shift = SENSITIVITY_FRACTION * (upper - lower)
    base = _sensitivity_case("base", base_plan, upper, lower)
    cases = []
    for u_sign, u_name in ((-1, "minus"), (1, "plus")):
        for l_sign, l_name in ((-1, "minus"), (1, "plus")):
            u, lo = upper + u_sign * shift, lower + l_sign * shift
            plan = evaluate_plan(contexts, u, lo, taw_per_m, root_depth_m, efficiency, area_m2, localized, interval_days)
            cases.append(_sensitivity_case(f"upper_{u_name}_lower_{l_name}", plan, u, lo))
    everything = [base, *cases]
    nets = [c.net_dose_mm for c in everything if c.net_dose_mm is not None]
    grosses = [c.gross_dose_mm for c in everything if c.gross_dose_mm is not None]
    return Sensitivity(
        shift_fraction_of_range=SENSITIVITY_FRACTION, shift_index_points=round(shift, 2), base=base,
        cases=tuple(cases),
        net_dose_min_mm=min(nets) if nets else None, net_dose_max_mm=max(nets) if nets else None,
        gross_dose_min_mm=min(grosses) if grosses else None, gross_dose_max_mm=max(grosses) if grosses else None,
        cases_without_irrigation=sum(1 for c in everything if c.irrigation_day is None),
    )


# --- Limitaciones y factores --------------------------------------------------------------------
_RELATIVE_SCALE = Limitation(
    code="relative_scale_indicative",
    message="La humedad es el índice relativo de 0 a 100 de la sonda (no humedad volumétrica): la recomendación es "
    "indicativa y la dosis depende de los anclajes de esa escala.",
)


def _days_text(days: Sequence[int]) -> str:
    return ", ".join(str(d) for d in days)


def _prediction_limitations(inp: EngineInput, contexts: Sequence[_DayContext]) -> list[Limitation]:
    out: list[Limitation] = []
    used_keys = {(d, c.day) for c in contexts for d in c.used}
    used = sorted((p for p in inp.predictions if (p.depth_cm, p.horizon_days) in used_keys),
                  key=lambda p: (p.depth_cm, p.horizon_days))

    # Sondas de la zona radicular sin predicción, con los días afectados
    missing: dict[int, list[int]] = {}
    for c in contexts:
        if c.used:  # los días sin ninguna sonda se avisan aparte
            for d in c.missing:
                missing.setdefault(d, []).append(c.day)
    for depth in sorted(missing):
        out.append(Limitation(
            code="missing_probe",
            message=f"La sonda de {depth} cm no tiene predicción los días {_days_text(missing[depth])}: se calcula con "
            "las demás sondas de la zona radicular.",
            params={"depth_cm": depth, "days": missing[depth]}, degrades_validation=True,
        ))
    no_data = [c.day for c in contexts if c.in_season and c.moisture_mean is None]
    if no_data:
        out.append(Limitation(
            code="days_without_data",
            message=f"Ninguna sonda de la zona radicular tiene predicción los días {_days_text(no_data)}: sin datos "
            "del suelo no se calcula dosis esos días.",
            params={"days": no_data}, degrades_validation=True,
        ))

    fallback = [(p.depth_cm, p.horizon_days) for p in used if p.method == "persistence_fallback"]
    if fallback:
        out.append(Limitation(
            code="persistence_fallback",
            message="Algunas predicciones son persistencia (la última lectura sin cambio) porque el modelo no pudo "
            "predecir: son menos fiables.",
            params={"probes": [{"depth_cm": d, "day": h} for d, h in fallback]}, degrades_validation=True,
        ))
    provisional = [(p.depth_cm, p.horizon_days) for p in used if p.quality == "provisional"]
    if provisional:
        out.append(Limitation(
            code="prediction_provisional",
            message="Algunas predicciones de humedad usadas son provisionales.",
            params={"probes": [{"depth_cm": d, "day": h} for d, h in provisional]}, degrades_validation=True,
        ))
    # Limitaciones que traen las predicciones usadas (códigos del módulo de ML), agrupadas por código
    by_code: dict[str, list[dict[str, object]]] = {}
    for p in used:
        for lim in p.limitations:
            extra = dict(sorted((lim.model_extra or {}).items()))
            by_code.setdefault(lim.code, []).append({"depth_cm": p.depth_cm, "day": p.horizon_days, **extra})
    for code in sorted(by_code):
        out.append(Limitation(
            code=code, message=f"Limitación de la predicción de humedad: {code}.",
            params={"probes": by_code[code]}, degrades_validation=True,
        ))
    return out


def _plot_limitations(
    plot: ReferencePlot, efficiency_override: float | None, taw_mm: float, localized: bool = False
) -> list[Limitation]:
    out: list[Limitation] = []
    if plot.status != "confirmed":
        out.append(Limitation(
            code="plot_not_confirmed", message="La parcela no está confirmada (status candidato).",
            degrades_validation=True,
        ))
    crop_name = plot.crop.value if plot.crop else None
    gc = plot.ground_cover
    if crop_name and (gc is None or gc.source == "assumption"):
        if gc is None:
            used = rv.kc_values(crop_name, rv.DEFAULT_KC_CASE)
            msg = (f"No se conoce la cubierta vegetal entre las filas: se usa el caso «sin cubierta» (Kc {used[0]:.2f} / "
                   f"{used[1]:.2f} / {used[2]:.2f}, Tabla 12), el más prudente.")
        else:
            msg = rv.ground_cover_limitation(crop_name, gc.value)
        out.append(Limitation(code="assumed_ground_cover", message=msg, degrades_validation=True))
    if plot.cycle_start_date is None or plot.cycle_start_date.source == "assumption":
        out.append(Limitation(
            code="assumed_crop_stage",
            message="La fecha de inicio del ciclo es desconocida: se supone el 1 de marzo (Tabla 11, «Deciduous Orchard», "
            "210 días). La etapa y el Kc son una estimación y la campaña se da por terminada el 26 de septiembre. "
            "Inicio y fin aprobados por Laia el 5 de noviembre de 2026.",
            degrades_validation=True,
        ))
    if efficiency_override is None and (plot.irrigation_type is None or plot.irrigation_type.source == "assumption"):
        out.append(Limitation(
            code="assumed_irrigation_efficiency",
            message="El tipo de riego es desconocido: se usa una eficiencia genérica de "
            f"{rv.GENERIC_IRRIGATION_EFFICIENCY.value:.2f} (valor intermedio de la tabla de la FAO).",
            degrades_validation=True,
        ))
    anchors = plot.anchors
    if anchors is not None:
        if anchors.lower.source == "generic":
            out.append(Limitation(
                code="generic_lower_anchor",
                message="El anclaje inferior sale de una conversión genérica, no de la configuración de la sonda "
                "ni de un periodo seco de los datos.",
                degrades_validation=True,
            ))
        pending = [n for n, a in (("superior", anchors.upper), ("inferior", anchors.lower)) if a.review == "pending"]
        if pending:
            out.append(Limitation(
                code="anchors_pending_review",
                message=f"Anclaje {' e '.join(pending)} sin revisar con Laia.", degrades_validation=True,
            ))
        m = anchors.observed_max_index
        if m is not None and m < anchors.upper.index:
            d = depletion_fraction(m, anchors.upper.index, anchors.lower.index)
            out.append(Limitation(
                code="upper_anchor_above_observed_max",
                message=f"El suelo lleno real de la sonda queda cerca de {m:.1f} (máximo observado) y no en "
                f"{anchors.upper.index:.1f}: con el suelo recién lleno la fracción gastada ya sale de {d:.2f} y el "
                f"agotamiento queda sobreestimado en unos {d * taw_mm:.0f} mm. "
                + ("Con el goteo afecta a cuándo se cruza el umbral y al tope de agotamiento de la dosis, no a la "
                   "reposición de la ETc. " if localized else "La dosis queda sobreestimada en esa cantidad. ")
                + "El análisis de sensibilidad lo cubre en parte.",
                params={"observed_max": m, "upper_anchor": round(anchors.upper.index, 2),
                        "depletion_at_observed_max": round(d, 3), "dose_overestimate_mm": round(d * taw_mm, 1),
                        "depletion_overestimate_mm": round(d * taw_mm, 1)},
            ))
    if localized:
        out.append(Limitation(
            code="localized_irrigation",
            message=(plot.localized_irrigation_note or "Riego localizado: la sonda mide un punto de la franja mojada.")
            + f" Las mm de la dosis son láminas sobre la franja mojada; repartidas sobre toda la superficie serían "
            f"del orden de fw = {rv.WETTED_FRACTION_TRICKLE.value} veces esas mm (Tabla 20, supuesto).",
            params={"wetted_fraction_fw": rv.WETTED_FRACTION_TRICKLE.value},
        ))
    return out


def _replenishment_limitations(interval: int, assumed: bool, irr: DayPlan | None) -> list[Limitation]:
    out = []
    if assumed:
        ref = rv.DRIP_REPLENISHMENT_INTERVAL_DAYS
        out.append(Limitation(
            code="assumed_replenishment_interval",
            message=f"No se conoce cada cuántos días riega el agricultor: se supone un intervalo de reposición de "
            f"{interval} días (supuesto sin cifra en FAO-56). Con otro intervalo, la dosis cambia en proporción.",
            params={"interval_days": interval, "source": ref.source}, degrades_validation=True,
        ))
    out.append(Limitation(
        code="drip_replenishment_dose",
        message=f"Riego por goteo: la dosis repone lo que gasta el cultivo (ETc) en {interval} días y no devuelve toda "
        "la zona radicular a capacidad de campo (FAO-56, capítulo 8, riego de alta frecuencia). Nunca supera el "
        "agotamiento actual ni el agua fácilmente aprovechable (RAW = p × TAW × Zr). La sonda decide cuándo regar. "
        "Los mm son sobre la franja mojada; sin lluvia descontada.",
        params={"interval_days": interval},
    ))
    if irr is not None and irr.dose_limit in ("depletion", "raw_cap", "no_etc"):
        why = {
            "depletion": "el agotamiento actual del suelo",
            "raw_cap": "el tope por evento (RAW)",
            "no_etc": "el agotamiento y el tope RAW, porque no hay ETc prevista",
        }[irr.dose_limit]
        out.append(Limitation(
            code="dose_limited", message=f"La dosis del día {irr.day} está limitada por {why}, no por la reposición "
            "de la ETc.", params={"day": irr.day, "limit": irr.dose_limit},
        ))
    return out


def _factor(name: str, value: float | str | None, unit: str, source: str, status: str, note: str = "") -> Factor:
    return Factor(name=name, value=value, unit=unit, source=source, status=status, note=note)


def _sourced_status(source: str) -> str:
    return "assumption" if source == "assumption" else "provided"


def _factors(
    plot: ReferencePlot, taw: rv.Reference, zr: rv.Reference, p_ref: rv.Reference, kc_case: str,
    kc_triplet: tuple[float, float, float], cycle_start: tuple[int, int], efficiency: rv.Reference | float,
    interval: rv.Reference | None = None,
) -> list[Factor]:
    anchors = plot.anchors
    assert anchors is not None and plot.crop and plot.soil_texture
    f = [
        _factor("crop", plot.crop.value, "", f"Parcela ({plot.crop.source})", _sourced_status(plot.crop.source), plot.crop.note),
        _factor("soil_texture", plot.soil_texture.value, "", f"Parcela ({plot.soil_texture.source})",
                _sourced_status(plot.soil_texture.source), plot.soil_texture.note),
        _factor("taw_mm_per_m", taw.value, taw.unit, taw.source, taw.status, taw.note),
        _factor("root_depth_m", zr.value, zr.unit, zr.source, zr.status, zr.note),
        _factor("taw_mm", round(taw.value * zr.value, 1), "mm", "TAW (mm/m) × Zr (m)", "derived"),
        _factor("depletion_fraction_p", p_ref.value, "fracción", p_ref.source, p_ref.status,
                "Valor de tabla; cada día se ajusta a su ETc (nota 2 de la Tabla 22)."),
        _factor("kc_case", kc_case, "", plot.ground_cover and f"Parcela ({plot.ground_cover.source})" or "Por defecto (sin cubierta)",
                _sourced_status(plot.ground_cover.source) if plot.ground_cover else "assumption",
                "Caso de la Tabla 12 de FAO-56; se supone heladas que matan la hoja."),
        _factor("kc_ini", kc_triplet[0], "", rv.crop(plot.crop.value).sources["kc"], "verified"),
        _factor("kc_mid", kc_triplet[1], "", rv.crop(plot.crop.value).sources["kc"], "verified"),
        _factor("kc_end", kc_triplet[2], "", rv.crop(plot.crop.value).sources["kc"], "verified"),
        _factor("cycle_start", f"{cycle_start[1]:02d}-{cycle_start[0]:02d}", "día-mes",
                plot.cycle_start_date and f"Parcela ({plot.cycle_start_date.source})" or rv.ASSUMED_CYCLE_START_SOURCE,
                _sourced_status(plot.cycle_start_date.source) if plot.cycle_start_date else "assumption",
                "Etapas y Kc por día con la Tabla 11 (20/70/90/30 días) e interpolación lineal."),
        _factor("anchor_upper", round(anchors.upper.index, 2), "índice 0-100", anchors.upper.method, "provided",
                f"Fuente {anchors.upper.source}; revisión {anchors.upper.review}."),
        _factor("anchor_lower", round(anchors.lower.index, 2), "índice 0-100", anchors.lower.method, "provided",
                f"Fuente {anchors.lower.source}; revisión {anchors.lower.review}."),
        _factor("wetted_fraction_fw", rv.WETTED_FRACTION_TRICKLE.value, "fracción", rv.WETTED_FRACTION_TRICKLE.source,
                "assumption", "Solo informativa: no se aplica a la dosis (FAO-56 usa fw para la evaporación del suelo, Ke)."),
        _factor("rain_postpone_threshold_mm", RAIN_POSTPONE_MM, "mm",
                "Umbral propio, sin fuente FAO-56; aprobado por Laia el 5 de noviembre de 2026", "provided",
                "Con esa lluvia prevista el mismo día, un riego necesario se aplaza."),
    ]
    if interval is not None:
        f.append(_factor("dose_criterion", "replenishment", "", "FAO-56, capítulo 8 (riego de alta frecuencia)", "verified",
                         "Dosis = reposición de la ETc del intervalo; topes: agotamiento actual y RAW = p × TAW × Zr "
                         "(FAO-56, ecuación 83). Los mm son sobre la franja mojada."))
        f.append(_factor("replenishment_interval_days", interval.value, interval.unit, interval.source,
                         interval.status, interval.note))
    if isinstance(efficiency, float):
        f.append(_factor("irrigation_efficiency", efficiency, "fracción", "Entrada del cálculo", "provided"))
    else:
        f.append(_factor("irrigation_efficiency", efficiency.value, efficiency.unit, efficiency.source,
                         efficiency.status, efficiency.note))
    if plot.area_m2 is not None:
        f.append(_factor("area_m2", plot.area_m2.value, "m²", f"Parcela ({plot.area_m2.source})",
                         _sourced_status(plot.area_m2.source), plot.area_m2.note))
    return f


# --- Motor --------------------------------------------------------------------------------------
def _qualitative_result(inp: EngineInput, missing: list[tuple[str, str]]) -> EngineResult:
    """Sin cultivo, textura o anclajes no se calcula dosis: recomendación cualitativa con el motivo."""
    plan = tuple(
        DayPlan(day=k, date=inp.origin_date + dt.timedelta(days=k), decision="insufficient_data",
                reasons=tuple(code for code, _ in missing))
        for k in range(1, PLAN_DAYS + 1)
    )
    limitations = [Limitation(code=code, message=msg, degrades_validation=True) for code, msg in missing]
    limitations.append(_RELATIVE_SCALE)
    return EngineResult(
        origin_date=inp.origin_date, period_start=plan[0].date, period_end=plan[-1].date,
        decision="insufficient_data", validation_status="insufficient_data", plan=plan,
        limitations=tuple(limitations),
    )


def run_engine(inp: EngineInput) -> EngineResult:
    """Calcula la recomendación de riego de 7 días. Función pura: mismas entradas, mismo resultado.

    Un cultivo, una textura o un tipo de riego fuera de las tablas lanzan `UnknownReferenceError`.
    """
    plot = inp.plot
    missing = []
    if plot.crop is None:
        missing.append(("missing_crop", "Falta el cultivo de la parcela: no se calcula dosis, solo una valoración cualitativa."))
    if plot.soil_texture is None:
        missing.append(("missing_soil_texture", "Falta la textura del suelo: no se calcula dosis, solo una valoración cualitativa."))
    if plot.anchors is None:
        missing.append(("missing_anchors", "Faltan los anclajes de la escala del sensor: no se puede pasar la humedad a agua útil."))
    if missing:
        return _qualitative_result(inp, missing)
    assert plot.crop and plot.soil_texture and plot.anchors

    crop_name = plot.crop.value
    taw = rv.total_available_water_per_m(plot.soil_texture.value)
    zr = rv.root_depth_for_scheduling(crop_name)
    p_ref = rv.depletion_fraction(crop_name)
    kc_case = rv.kc_case(plot.ground_cover.value if plot.ground_cover else None)
    kc_triplet = rv.kc_values(crop_name, kc_case)
    localized = rv.is_localized(plot.irrigation_type.value if plot.irrigation_type else None)
    interval_ref: rv.Reference | None = None
    if localized:
        if inp.replenishment_interval_days is not None:
            interval_ref = rv.Reference(
                inp.replenishment_interval_days, "días", "Entrada del cálculo", "provided",
                "Intervalo de reposición declarado para este cálculo.",
            )
        else:
            interval_ref = rv.DRIP_REPLENISHMENT_INTERVAL_DAYS
    interval_days = int(interval_ref.value) if interval_ref else 1
    cs = plot.cycle_start_date.value if plot.cycle_start_date else None
    cycle_start = (cs.month, cs.day) if cs else rv.ASSUMED_CYCLE_START
    if inp.irrigation_efficiency is not None:
        efficiency: rv.Reference | float = float(inp.irrigation_efficiency)
        ea = float(inp.irrigation_efficiency)
    else:
        efficiency = rv.irrigation_efficiency(plot.irrigation_type.value if plot.irrigation_type else None)
        ea = efficiency.value
    taw_mm = taw.value * zr.value
    area = plot.area_m2.value if plot.area_m2 else None
    upper, lower = plot.anchors.upper.index, plot.anchors.lower.index

    contexts = _build_contexts(inp, crop_name, kc_case, cycle_start)
    plan = evaluate_plan(contexts, upper, lower, taw.value, zr.value, ea, area, localized, interval_days)

    limitations = _plot_limitations(plot, inp.irrigation_efficiency, taw_mm, localized)
    if interval_ref is not None:
        limitations += _replenishment_limitations(
            interval_days, interval_ref.status == "assumption", first_irrigation(plan)
        )
    limitations += _prediction_limitations(inp, contexts)
    no_eto = [c.day for c in contexts if c.in_season and c.eto is None]
    if no_eto:
        limitations.append(Limitation(
            code="missing_eto",
            message=f"Falta la ET₀ prevista los días {_days_text(no_eto)}: no hay ETc y p se queda en el valor de la "
            "tabla (ETc ≈ 5 mm/día).",
            params={"days": no_eto}, degrades_validation=True,
        ))
    no_rain = [c.day for c in contexts if c.in_season and c.rain is None]
    if no_rain:
        limitations.append(Limitation(
            code="missing_rain", message=f"Falta la lluvia prevista los días {_days_text(no_rain)}: no se puede "
            "aplazar un riego por lluvia esos días.", params={"days": no_rain},
        ))
    off = [c.day for c in contexts if not c.in_season]
    if off:
        limitations.append(Limitation(
            code="out_of_season",
            message=f"Los días {_days_text(off)} están fuera de la campaña de riego del cultivo (reposo): no se "
            "recomienda regar y no se calcula dosis.",
            params={"days": off},
        ))
    limitations.append(_RELATIVE_SCALE)

    if all(d.decision == "insufficient_data" for d in plan):
        status = "insufficient_data"
        limitations.append(Limitation(
            code="no_soil_data", message="Ninguna sonda de la zona radicular tiene predicción: no hay dosis.",
            degrades_validation=True,
        ))
    elif any(lim.degrades_validation for lim in limitations):
        status = "provisional"
    else:
        status = "validated"

    irr = first_irrigation(plan)
    sens = None
    if status != "insufficient_data":
        sens = sensitivity_analysis(contexts, upper, lower, taw.value, zr.value, ea, area, plan, localized, interval_days)

    return EngineResult(
        origin_date=inp.origin_date, period_start=plan[0].date, period_end=plan[-1].date,
        decision=plan[0].decision, validation_status=status, plan=tuple(plan),
        irrigation_day=irr.day if irr else None, irrigation_date=irr.date if irr else None,
        net_dose_mm=irr.net_dose_mm if irr else None, gross_dose_mm=irr.gross_dose_mm if irr else None,
        volume_m3=irr.volume_m3 if irr else None,
        factors=tuple(_factors(plot, taw, zr, p_ref, kc_case, kc_triplet, cycle_start, efficiency, interval_ref)),
        dose_criterion="replenishment" if localized else "field_capacity",
        replenishment_interval_days=interval_days if localized else None, dose_on_wetted_strip=localized,
        limitations=tuple(limitations), sensitivity=sens,
    )
