"""El motor: escenarios de la especificación y de las reglas de decisión, con datos sintéticos."""

import datetime as dt

import pytest

from irrigation_app.irrigation_engine import SensorPrediction, run_engine
from irrigation_app.irrigation_engine import calculation as calc
from irrigation_app.irrigation_engine import reference_values as rv
from irrigation_app.reference_plot import REFERENCE_PLOT, Sourced

from irrigation_engine_factory import (
    CROSS_ON_DAY_4, WET, complete_plot, engine_input, plot_with, predictions, weather,
)

UPPER = REFERENCE_PLOT.anchors.upper.index
LOWER = REFERENCE_PLOT.anchors.lower.index


def decisions(result):
    return [d.decision for d in result.plan]


def codes(result):
    return {lim.code for lim in result.limitations}


# --- funciones puras -----------------------------------------------------------------------------
def test_depletion_formula_and_anchors():
    assert calc.depletion_fraction(UPPER, UPPER, LOWER) == 0
    assert calc.depletion_fraction(LOWER, UPPER, LOWER) == 1
    assert calc.depletion_fraction(69.55, 79.4, 59.7) == pytest.approx(0.5, abs=1e-3)
    assert calc.clamp_unit(-0.2) == 0 and calc.clamp_unit(1.3) == 1


def test_etc_net_gross_and_volume_formulas():
    assert calc.etc_mm(1.2, 5.0) == pytest.approx(6.0)
    assert calc.net_dose_mm(0.5, 155.0, 1.0) == pytest.approx(77.5)
    assert calc.gross_dose_mm(77.5, 0.9) == pytest.approx(86.11, abs=0.01)
    assert calc.volume_m3(90.0, 2000.0) == pytest.approx(180.0)
    assert calc.volume_m3(90.0, None) is None
    with pytest.raises(ValueError):
        calc.gross_dose_mm(10, 0)


def test_kc_follows_the_fao_curve():
    case = rv.kc_case("active_cover")
    assert rv.kc_at("manzano", case, 0) == ("ini", 0.5)
    assert rv.kc_at("manzano", case, 89) == ("dev", 1.2)  # final del desarrollo = Kc mid
    assert rv.kc_at("manzano", case, 100) == ("mid", 1.2)
    stage, kc = rv.kc_at("manzano", case, 209)
    assert stage == "late" and kc == 0.95  # final de la etapa tardía = Kc fin
    assert rv.kc_at("manzano", case, 210) is None and rv.kc_at("manzano", case, -1) is None


# --- escenarios de la especificación --------------------------------------------------------------
def test_probe_of_55_cm_is_outside_the_root_zone():
    base = predictions(CROSS_ON_DAY_4)
    extra = [
        SensorPrediction(depth_cm=55, horizon_days=k, moisture_pred=10.0, quality="validated", method="model")
        for k in range(1, 8)
    ]
    with_55 = run_engine(engine_input(None, preds=base + extra))
    without = run_engine(engine_input(None, preds=base))
    assert with_55 == without
    assert all(55 not in d.probes_used_cm for d in with_55.plan)


def test_crossing_on_day_4_irrigates_that_day_with_net_and_gross_mm():
    r = run_engine(engine_input(CROSS_ON_DAY_4))
    assert decisions(r) == ["do_not_irrigate"] * 3 + ["irrigate"] + ["do_not_irrigate"] * 3
    assert r.plan[3].reasons == ("threshold_crossed",) and r.plan[4].reasons == ("after_irrigation",)
    assert r.irrigation_day == 4 and r.irrigation_date == dt.date(2026, 8, 29)
    # goteo: reposición de la ETc de 3 días (días 4, 5 y 6 del plan), no el agotamiento completo
    day4 = r.plan[3]
    etc_3_days = sum(d.etc_mm for d in r.plan[3:6])
    assert r.net_dose_mm == pytest.approx(etc_3_days, abs=0.1) and day4.dose_limit == "replenishment"
    assert day4.depletion_mm == pytest.approx(((UPPER - 69.0) / (UPPER - LOWER)) * 155.0, abs=0.1)
    assert r.net_dose_mm < day4.depletion_mm
    assert r.gross_dose_mm == pytest.approx(r.net_dose_mm / 0.90, abs=0.1)
    assert r.plan[3].depletion > r.plan[3].p > r.plan[2].depletion
    assert r.decision == "do_not_irrigate"  # la del primer día del plan


def test_without_area_there_is_no_volume_and_with_area_there_is():
    r = run_engine(engine_input(CROSS_ON_DAY_4))
    assert r.volume_m3 is None and r.plan[3].volume_m3 is None
    plot = plot_with(area_m2=Sourced(value=2000.0, source="laia"))
    r2 = run_engine(engine_input(CROSS_ON_DAY_4, plot=plot))
    assert r2.volume_m3 == pytest.approx(r2.gross_dose_mm * 2.0, abs=0.1)


def test_no_crossing_means_do_not_irrigate_all_seven_days():
    r = run_engine(engine_input(WET))
    assert decisions(r) == ["do_not_irrigate"] * 7
    assert r.irrigation_day is None and r.net_dose_mm is None and r.gross_dose_mm is None
    assert all(d.reasons == ("below_threshold",) for d in r.plan)


def test_a_failing_probe_gives_provisional_and_names_the_probe():
    preds = [p for p in predictions(CROSS_ON_DAY_4) if not (p.depth_cm == 25 and p.horizon_days == 3)]
    r = run_engine(engine_input(None, preds=preds))
    assert r.validation_status == "provisional"
    lim = next(lim for lim in r.limitations if lim.code == "missing_probe")
    assert lim.params == {"depth_cm": 25, "days": [3]} and "25 cm" in lim.message
    assert r.plan[2].probes_used_cm == (15, 35) and r.plan[2].probes_missing_cm == (25,)


def test_all_probes_insufficient_data_gives_no_dose():
    preds = [
        SensorPrediction(depth_cm=d, horizon_days=k, quality="insufficient_data") for d in (15, 25, 35) for k in range(1, 8)
    ]
    r = run_engine(engine_input(None, preds=preds))
    assert r.validation_status == "insufficient_data" and r.decision == "insufficient_data"
    assert decisions(r) == ["insufficient_data"] * 7
    assert r.net_dose_mm is None and r.gross_dose_mm is None and r.sensitivity is None
    assert "no_soil_data" in codes(r)


def test_a_day_with_no_probe_is_insufficient_but_the_rest_are_calculated():
    preds = [p for p in predictions(CROSS_ON_DAY_4) if p.horizon_days != 2]
    r = run_engine(engine_input(None, preds=preds))
    assert r.plan[1].decision == "insufficient_data" and r.plan[3].decision == "irrigate"
    assert r.validation_status == "provisional" and "days_without_data" in codes(r)


def test_missing_crop_or_texture_gives_a_qualitative_recommendation():
    cand = plot_with(status="candidate", crop=None, confirmed_on=None)
    r = run_engine(engine_input(CROSS_ON_DAY_4, plot=cand))
    assert r.validation_status == "insufficient_data" and r.net_dose_mm is None
    assert "missing_crop" in codes(r) and decisions(r) == ["insufficient_data"] * 7
    cand2 = plot_with(status="candidate", soil_texture=None, confirmed_on=None)
    assert "missing_soil_texture" in codes(run_engine(engine_input(CROSS_ON_DAY_4, plot=cand2)))


def test_unknown_crop_or_texture_is_a_clear_error_not_an_invented_value():
    with pytest.raises(rv.UnknownReferenceError, match="Cultivo desconocido"):
        run_engine(engine_input(CROSS_ON_DAY_4, plot=plot_with(crop=Sourced(value="kiwi", source="laia"))))
    with pytest.raises(rv.UnknownReferenceError, match="Textura desconocida"):
        run_engine(engine_input(CROSS_ON_DAY_4, plot=plot_with(soil_texture=Sourced(value="arena", source="laia"))))


# --- supuestos y limitaciones ----------------------------------------------------------------------
def test_reference_plot_declares_its_assumptions():
    r = run_engine(engine_input(CROSS_ON_DAY_4))
    assert {"assumed_ground_cover", "assumed_crop_stage", "relative_scale_indicative", "localized_irrigation"} <= codes(r)
    assert "assumed_irrigation_efficiency" not in codes(r)  # el goteo lo dio Laia
    assert r.validation_status == "provisional" and r.indicative is True
    cover = next(lim for lim in r.limitations if lim.code == "assumed_ground_cover")
    assert "-21%" in cover.message  # sin cubierta, el Kc de pleno verano baja un 21 %


def test_unknown_irrigation_type_uses_generic_efficiency_and_says_so():
    r = run_engine(engine_input(CROSS_ON_DAY_4, plot=plot_with(irrigation_type=None)))
    assert "assumed_irrigation_efficiency" in codes(r)
    ea = next(f for f in r.factors if f.name == "irrigation_efficiency")
    assert ea.value == 0.75 and ea.status == "assumption"
    assert r.gross_dose_mm == pytest.approx(r.net_dose_mm / 0.75, abs=0.1)


def test_efficiency_input_overrides_the_table():
    r = run_engine(engine_input(CROSS_ON_DAY_4, irrigation_efficiency=0.8))
    assert r.gross_dose_mm == pytest.approx(r.net_dose_mm / 0.8, abs=0.15)
    assert next(f for f in r.factors if f.name == "irrigation_efficiency").status == "provided"


def test_upper_anchor_above_observed_max_limitation_quantifies_the_bias():
    r = run_engine(engine_input(CROSS_ON_DAY_4))
    lim = next(lim for lim in r.limitations if lim.code == "upper_anchor_above_observed_max")
    d = (UPPER - 76.7) / (UPPER - LOWER)
    assert lim.params["observed_max"] == 76.7 and lim.params["depletion_at_observed_max"] == pytest.approx(d, abs=1e-3)
    assert lim.params["dose_overestimate_mm"] == pytest.approx(d * 155, abs=0.1)  # unos 21 mm
    assert "76,7" in lim.message and "79,4" in lim.message
    assert lim.degrades_validation is False


def test_complete_plot_without_assumptions_is_validated():
    r = run_engine(engine_input(CROSS_ON_DAY_4, plot=complete_plot(), replenishment_interval_days=3))
    assert r.validation_status == "validated"
    assert not any(lim.degrades_validation for lim in r.limitations)
    assert r.indicative is True  # validada no quita la escala relativa


def test_persistence_fallback_and_ml_limitations_make_it_provisional_and_are_copied():
    preds = predictions(CROSS_ON_DAY_4, limitations=[{"code": "stale_reading", "age_hours": 50}])
    preds = [p.model_copy(update={"method": "persistence_fallback", "quality": "provisional"}) for p in preds]
    r = run_engine(engine_input(None, plot=complete_plot(), preds=preds))
    assert r.validation_status == "provisional"
    assert {"persistence_fallback", "prediction_provisional", "stale_reading"} <= codes(r)
    stale = next(lim for lim in r.limitations if lim.code == "stale_reading")
    assert stale.params["probes"][0] == {"depth_cm": 15, "day": 1, "age_hours": 50}


def test_missing_eto_keeps_table_p_and_is_provisional():
    r = run_engine(engine_input(CROSS_ON_DAY_4, plot=complete_plot(), wx=[]))
    assert r.validation_status == "provisional" and "missing_eto" in codes(r)
    assert all(d.etc_mm is None and d.p == 0.5 for d in r.plan)


def test_p_is_adjusted_to_the_etc_of_each_day():
    low = run_engine(engine_input(WET, wx=weather(eto=3.0)))
    high = run_engine(engine_input(WET, wx=weather(eto=7.0)))
    assert low.plan[0].p > 0.5 > high.plan[0].p
    assert low.plan[0].etc_mm == pytest.approx(3.6)  # Kc 1,20 × 3 mm


def test_depletion_is_clamped_between_0_and_1():
    r = run_engine(engine_input({k: 85.0 for k in range(1, 8)}, preds=None))
    assert r.plan[0].depletion == 0 and r.plan[0].depletion_clamped is True
    dry = run_engine(engine_input({k: 40.0 for k in range(1, 8)}))
    assert dry.plan[0].depletion == 1 and dry.plan[0].depletion_mm == pytest.approx(155.0)
    assert dry.net_dose_mm < 155.0  # el goteo repone la ETc, no llena la zona radicular


# --- lluvia ---------------------------------------------------------------------------------------
def test_rain_on_the_crossing_day_postpones_and_the_next_dry_day_irrigates():
    wx = weather(rain_by_day={4: 12.0})
    r = run_engine(engine_input(CROSS_ON_DAY_4, wx=wx))
    assert decisions(r)[:5] == ["do_not_irrigate"] * 3 + ["postpone", "irrigate"]
    assert r.plan[3].reasons == ("threshold_crossed", "rain_forecast") and r.plan[3].net_dose_mm is None
    assert r.irrigation_day == 5


def test_rain_is_not_added_to_the_predicted_moisture():
    dry = run_engine(engine_input(WET, wx=weather(rain=0.0)))
    wet = run_engine(engine_input(WET, wx=weather(rain=30.0)))
    assert [d.depletion for d in dry.plan] == [d.depletion for d in wet.plan]


# --- fuera de campaña -------------------------------------------------------------------------------
def test_out_of_season_dates_do_not_irrigate_and_have_no_dose():
    origin = dt.date(2026, 11, 5)
    r = run_engine(engine_input({k: 40.0 for k in range(1, 8)}, origin=origin, wx=weather(origin)))
    assert decisions(r) == ["do_not_irrigate"] * 7
    assert all(d.reasons == ("out_of_season",) and d.kc is None and d.net_dose_mm is None for d in r.plan)
    assert "out_of_season" in codes(r) and r.net_dose_mm is None


def test_cycle_start_date_defines_the_season():
    origin = dt.date(2026, 10, 20)  # sin fecha de inicio: el ciclo supuesto acaba el 26 de septiembre
    levels = {k: 70.0 for k in range(1, 8)}
    assumed = run_engine(engine_input(levels, origin=origin, wx=weather(origin)))
    assert all(d.reasons == ("out_of_season",) for d in assumed.plan)
    plot = plot_with(cycle_start_date=Sourced(value=dt.date(2026, 4, 15), source="laia"))
    known = run_engine(engine_input(levels, origin=origin, wx=weather(origin), plot=plot))
    assert all(d.stage == "late" for d in known.plan[:3])  # 15 de abril + 180 días = 12 de octubre
    assert "assumed_crop_stage" in codes(assumed) and "assumed_crop_stage" not in codes(known)


def test_the_season_can_end_inside_the_plan():
    origin = dt.date(2026, 9, 23)  # 24 sep (dentro) ... 26 sep (último día) ... 27 sep (fuera)
    r = run_engine(engine_input({k: 70.0 for k in range(1, 8)}, origin=origin, wx=weather(origin)))
    assert [d.stage is not None for d in r.plan] == [True, True, True, False, False, False, False]
    assert next(lim for lim in r.limitations if lim.code == "out_of_season").params == {"days": [4, 5, 6, 7]}


# --- sensibilidad ----------------------------------------------------------------------------------
def test_sensitivity_gives_min_and_max_dose_and_the_indicative_mark():
    r = run_engine(engine_input(CROSS_ON_DAY_4))
    s = r.sensitivity
    assert r.indicative is True and "relativo" in r.indicative_note
    assert s.shift_fraction_of_range == 0.10 and s.shift_index_points == pytest.approx(1.97, abs=0.01)
    assert len(s.cases) == 4
    assert s.base.net_dose_mm == r.net_dose_mm
    assert s.net_dose_min_mm <= r.net_dose_mm <= s.net_dose_max_mm
    assert s.gross_dose_min_mm <= r.gross_dose_mm <= s.gross_dose_max_mm
    assert sorted({c.upper_anchor for c in s.cases}) == pytest.approx(
        [UPPER - s.shift_index_points, UPPER + s.shift_index_points], abs=0.011
    )


def test_sensitivity_without_irrigation_has_no_dose_range():
    s = run_engine(engine_input(WET)).sensitivity
    assert s.net_dose_min_mm is None and s.cases_without_irrigation >= 1


# --- factores --------------------------------------------------------------------------------------
def test_factors_carry_value_and_origin():
    r = run_engine(engine_input(CROSS_ON_DAY_4))
    f = {x.name: x for x in r.factors}
    assert f["taw_mm_per_m"].value == 155.0 and "Tabla 19" in f["taw_mm_per_m"].source
    assert f["root_depth_m"].value == 1.0 and "Tabla 22" in f["root_depth_m"].source
    assert f["kc_mid"].value == 1.2 and "Tabla 12" in f["kc_mid"].source
    assert f["kc_case"].value == "active_cover_killing_frost" and f["kc_case"].status == "assumption"
    assert f["anchor_upper"].value == pytest.approx(79.4, abs=0.05)
    assert "Agrodevices" in f["anchor_upper"].source
    assert f["cycle_start"].value == "01-03" and f["cycle_start"].status == "assumption"
    assert f["wetted_fraction_fw"].status == "assumption"


# --- dosis del goteo: reposición de la ETc del intervalo -------------------------------------------------
def test_replenishment_dose_function_takes_the_smallest_of_etc_depletion_and_raw():
    assert calc.replenishment_dose_mm([6, 6, 6], 3, 80.0, 70.0) == (18.0, 18.0, "replenishment")
    assert calc.replenishment_dose_mm([6, 6, 6], 3, 10.0, 70.0) == (10.0, 18.0, "depletion")
    assert calc.replenishment_dose_mm([6, 6, 6], 3, 80.0, 12.0) == (12.0, 18.0, "raw_cap")
    # ventana más corta que el intervalo (final del plan) o con huecos: media de los días conocidos x intervalo
    assert calc.replenishment_dose_mm([6, None], 3, 80.0, 70.0)[0] == pytest.approx(18.0)
    assert calc.replenishment_dose_mm([None, None], 3, 80.0, 70.0) == (70.0, None, "no_etc")


def test_drip_dose_replenishes_the_etc_of_the_interval_and_declares_it():
    r = run_engine(engine_input(CROSS_ON_DAY_4))
    assert r.dose_criterion == "replenishment" and r.replenishment_interval_days == 3
    assert r.dose_on_wetted_strip is True
    d = r.plan[3]
    assert d.etc_interval_mm == pytest.approx(sum(x.etc_mm for x in r.plan[3:6]), abs=0.1)
    assert codes(r) >= {"assumed_replenishment_interval", "drip_replenishment_dose"}
    assumed = next(lim for lim in r.limitations if lim.code == "assumed_replenishment_interval")
    assert assumed.degrades_validation and assumed.params["interval_days"] == 3
    f = next(f for f in r.factors if f.name == "replenishment_interval_days")
    assert f.value == 3 and f.status == "assumption"


def test_declared_interval_changes_the_dose_proportionally_and_is_not_an_assumption():
    one = run_engine(engine_input(CROSS_ON_DAY_4, replenishment_interval_days=1))
    five = run_engine(engine_input(CROSS_ON_DAY_4, replenishment_interval_days=5))
    assert one.replenishment_interval_days == 1 and five.replenishment_interval_days == 5
    assert five.net_dose_mm == pytest.approx(5 * one.net_dose_mm, rel=0.05)
    assert "assumed_replenishment_interval" not in codes(five)
    assert next(f for f in five.factors if f.name == "replenishment_interval_days").status == "provided"


def test_the_interval_is_validated():
    with pytest.raises(ValueError):
        engine_input(CROSS_ON_DAY_4, replenishment_interval_days=0)
    with pytest.raises(ValueError):
        engine_input(CROSS_ON_DAY_4, replenishment_interval_days=8)


def test_event_cap_raw_binds_with_extreme_etc_and_is_reported():
    # ET0 de 25 mm/día: p baja a 0,1 y RAW = 0,1 x 155 = 15,5 mm, menos que la ETc de 3 días
    r = run_engine(engine_input(CROSS_ON_DAY_4, wx=weather(eto=25.0)))
    d = r.plan[r.irrigation_day - 1]
    assert d.dose_limit == "raw_cap" and d.raw_cap_mm == pytest.approx(0.1 * 155.0, abs=0.1)
    assert r.net_dose_mm == pytest.approx(d.raw_cap_mm, abs=0.1)
    assert r.net_dose_mm < d.etc_interval_mm
    assert "dose_limited" in codes(r)


def test_net_dose_never_exceeds_the_current_depletion():
    for eto in (0.5, 2.0, 5.0, 12.0, 25.0):
        for levels in (CROSS_ON_DAY_4, {k: 40.0 for k in range(1, 8)}, {k: 66.0 for k in range(1, 8)}):
            for interval in (1, 3, 7):
                r = run_engine(engine_input(levels, wx=weather(eto=eto), replenishment_interval_days=interval))
                for d in r.plan:
                    if d.decision == "irrigate":
                        assert d.net_dose_mm <= d.depletion_mm + 0.05
                        assert d.net_dose_mm <= d.raw_cap_mm + 0.05


def test_without_eto_the_dose_falls_back_to_the_cap_and_says_so():
    r = run_engine(engine_input(CROSS_ON_DAY_4, wx=[]))
    d = r.plan[r.irrigation_day - 1]
    assert d.dose_limit == "no_etc" and d.etc_interval_mm is None
    assert d.net_dose_mm == pytest.approx(d.raw_cap_mm, abs=0.1)
    assert "missing_eto" in codes(r)


def test_dose_at_the_end_of_the_plan_uses_the_days_that_remain():
    late = {1: 60.0, 2: 60.0, 3: 60.0, 4: 60.0, 5: 60.0, 6: 60.0, 7: 60.0}
    r = run_engine(engine_input(late, replenishment_interval_days=7))  # cruza el día 1; ventana de 7 días
    assert r.irrigation_day == 1
    r6 = run_engine(engine_input({**{k: 76.0 for k in range(1, 7)}, 7: 60.0}, replenishment_interval_days=3))
    assert r6.irrigation_day == 7  # solo queda 1 día en la ventana: se usa su ETc x 3
    assert r6.net_dose_mm == pytest.approx(3 * r6.plan[6].etc_mm, abs=0.1)


def test_non_localized_irrigation_keeps_the_field_capacity_criterion():
    r = run_engine(engine_input(CROSS_ON_DAY_4, plot=plot_with(irrigation_type=None)))
    d = r.plan[3]
    assert r.dose_criterion == "field_capacity" and r.replenishment_interval_days is None
    assert r.dose_on_wetted_strip is False and d.dose_limit == "field_capacity"
    assert r.net_dose_mm == pytest.approx(d.depletion_mm, abs=0.1)
    assert "assumed_replenishment_interval" not in codes(r) and "localized_irrigation" not in codes(r)


def test_sensitivity_keeps_working_with_the_new_criterion():
    r = run_engine(engine_input(CROSS_ON_DAY_4))
    s = r.sensitivity
    assert s.net_dose_min_mm <= r.net_dose_mm <= s.net_dose_max_mm
    assert len(s.cases) == 4


def test_user_texts_use_decimal_comma():
    import re
    r = run_engine(engine_input(CROSS_ON_DAY_4))
    texts = [lim.message for lim in r.limitations] + [f.note for f in r.factors]
    assert texts and all(not re.search(r"\d\.\d", t) for t in texts)
