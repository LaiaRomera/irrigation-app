"""Pruebas de las marcas de riego e instalación, con datos sintéticos."""

import pandas as pd

from irrigation_app.irrigation_marks import (
    admin_days,
    installation_days,
    installation_mask,
    installation_summary,
    interval_rain,
    possible_irrigation,
)


def make_snapshots(series, start="2026-01-01"):
    """`series`: {(unit, depth): [índices por día consecutivo, None = día sin foto]}."""
    frames = []
    for (unit, depth), values in series.items():
        days = pd.date_range(start, periods=len(values), freq="D", tz="UTC")
        frame = pd.DataFrame({"unit_id": unit, "day": days, "depth_cm": depth, "moisture_index": values})
        frames.append(frame[frame["moisture_index"].notna()])
    return pd.concat(frames, ignore_index=True)


def day(text):
    return pd.Timestamp(text, tz="UTC")


# ---------- Posible riego (2.1) ----------

def test_a_rise_without_rain_is_marked():
    snapshots = make_snapshots({(1, 10): [50.0, 49.0, 55.0, 54.0]})
    marks = possible_irrigation(snapshots).set_index("day")["possible_irrigation"]
    assert marks[day("2026-01-03")]
    assert not marks[day("2026-01-02")] and not marks[day("2026-01-04")]


def test_the_first_day_has_no_rise_and_is_not_marked():
    marks = possible_irrigation(make_snapshots({(1, 10): [50.0, 50.0]}))
    first = marks[marks["day"] == day("2026-01-01")]
    assert first.empty or not first["possible_irrigation"].any()


def test_a_rise_with_rain_is_not_marked_but_with_little_rain_it_is():
    snapshots = make_snapshots({(1, 10): [50.0, 56.0, 62.0]})
    rain = pd.DataFrame({"unit_id": 1, "day": [day("2026-01-02"), day("2026-01-03")], "rain_mm": [10.0, 0.5]})
    marks = possible_irrigation(snapshots, rain=rain, rain_limit_mm=2.0).set_index("day")["possible_irrigation"]
    assert not marks[day("2026-01-02")]
    assert marks[day("2026-01-03")]


def test_unknown_rain_does_not_rule_out_irrigation():
    snapshots = make_snapshots({(1, 10): [50.0, 56.0]})
    rain = pd.DataFrame({"unit_id": [1], "day": [day("2026-01-02")], "rain_mm": [float("nan")]})
    assert possible_irrigation(snapshots, rain=rain).set_index("day").loc[day("2026-01-02"), "possible_irrigation"]


def test_a_gap_day_gives_no_rise():
    snapshots = make_snapshots({(1, 10): [50.0, None, 60.0]})
    marks = possible_irrigation(snapshots)
    assert not marks["possible_irrigation"].any()
    assert marks.loc[marks["day"] == day("2026-01-03"), "rise"].isna().all()


def test_depth_aggregation_mean_versus_any():
    # Una sola profundidad sube 6; la otra no se mueve: media 3, cualquiera 6.
    snapshots = make_snapshots({(1, 10): [50.0, 56.0], (1, 20): [60.0, 60.0]})
    mean_marks = possible_irrigation(snapshots, rise_threshold=4.0, depth_agg="mean")
    any_marks = possible_irrigation(snapshots, rise_threshold=4.0, depth_agg="any")
    assert not mean_marks["possible_irrigation"].any()
    assert any_marks.set_index("day").loc[day("2026-01-02"), "possible_irrigation"]
    assert mean_marks.set_index("day").loc[day("2026-01-02"), "rise"] == 3.0


def test_units_are_marked_independently():
    snapshots = make_snapshots({(1, 10): [50.0, 60.0], (2, 10): [50.0, 50.0]})
    marks = possible_irrigation(snapshots).set_index(["unit_id", "day"])["possible_irrigation"]
    assert marks[(1, day("2026-01-02"))] and not marks[(2, day("2026-01-02"))]


def test_an_unknown_aggregation_is_rejected():
    snapshots = make_snapshots({(1, 10): [50.0, 60.0]})
    for function in (possible_irrigation, installation_summary):
        try:
            function(snapshots, depth_agg="max")
        except ValueError:
            continue
        raise AssertionError("debía rechazar depth_agg desconocido")


# ---------- Días de instalación (2.2) ----------

def test_unit_1168_loses_the_installation_days():
    # Como la 1168: valores de 2 a 10 durante cuatro días y, de golpe, el suelo enterrado.
    snapshots = make_snapshots(
        {
            (1168, 15): [2.0, 6.3, 9.6, 7.8, 71.8, 71.8, 72.5],
            (1168, 55): [6.7, 8.5, 8.4, 9.0, 68.7, 68.4, 68.4],
        },
        start="2025-03-09",
    )
    days = installation_days(snapshots)
    assert days["day"].tolist() == [day("2025-03-09"), day("2025-03-10"), day("2025-03-11"), day("2025-03-12")]
    assert set(days["unit_id"]) == {1168}


def test_a_unit_that_dries_below_the_threshold_after_working_is_not_installation():
    values = [70.0, 60.0, 45.0, 30.0, 18.0, 12.0, 8.0, 75.0]
    snapshots = make_snapshots({(7, 10): values}, start="2026-07-01")
    assert installation_days(snapshots).empty


def test_a_unit_that_starts_plausible_is_not_marked_even_if_it_has_low_values_later():
    snapshots = make_snapshots({(7, 10): [40.0, 30.0, 15.0, 12.0, 14.0, 60.0]})
    assert installation_summary(snapshots).empty


def test_the_installation_ends_the_day_before_the_first_plausible_value():
    snapshots = make_snapshots({(3, 10): [5.0, 6.0, 80.0, 5.0, 4.0]})
    summary = installation_summary(snapshots).iloc[0]
    assert summary["first_day"] == day("2026-01-01")
    assert summary["last_day"] == day("2026-01-02")
    assert summary["days_marked"] == 2
    assert summary["recovered"]


def test_the_threshold_is_a_parameter():
    snapshots = make_snapshots({(3, 10): [25.0, 25.0, 80.0]})
    assert installation_days(snapshots).empty
    assert len(installation_days(snapshots, threshold=30.0)) == 2


def test_the_installation_window_has_a_cap_and_reports_no_recovery():
    snapshots = make_snapshots({(5, 10): [10.0] * 50 + [70.0]})
    summary = installation_summary(snapshots, max_days=30).iloc[0]
    assert summary["days_marked"] == 30
    assert not summary["recovered"]


def test_a_series_that_never_recovers_is_marked_only_while_it_has_data():
    summary = installation_summary(make_snapshots({(5, 10): [10.0, 9.0, 8.0]})).iloc[0]
    assert summary["days_marked"] == 3
    assert not summary["recovered"]


def test_gap_days_inside_the_installation_are_marked_too():
    snapshots = make_snapshots({(3, 10): [5.0, None, None, 6.0, 80.0]})
    assert len(installation_days(snapshots)) == 4


def test_any_depth_keeps_the_unit_in_installation_while_the_mean_leaves_it():
    # Una profundidad sigue sin enterrar (5) y la otra ya está en 80: media 42, mínimo 5.
    snapshots = make_snapshots({(4, 10): [5.0, 5.0, 5.0, 5.0], (4, 20): [5.0, 5.0, 80.0, 80.0]})
    assert len(installation_days(snapshots, depth_agg="any")) == 4
    assert len(installation_days(snapshots, depth_agg="mean")) == 2


def test_the_manual_list_is_added_to_the_automatic_rule():
    snapshots = make_snapshots({(1, 10): [70.0] * 6, (2, 10): [5.0, 80.0, 80.0, 80.0, 80.0, 80.0]})
    days = installation_days(snapshots, manual={1: [("2026-01-03", "2026-01-04")]})
    pairs = set(zip(days["unit_id"], days["day"]))
    assert pairs == {(1, day("2026-01-03")), (1, day("2026-01-04")), (2, day("2026-01-01"))}


def test_installation_mask_matches_unit_and_day():
    snapshots = make_snapshots({(3, 10): [5.0, 80.0, 80.0], (4, 10): [80.0, 80.0, 80.0]})
    mask = installation_mask(snapshots, installation_days(snapshots))
    assert mask.tolist() == [True, False, False, False, False, False]


def test_summary_counts_days_per_unit():
    snapshots = make_snapshots({(3, 10): [5.0, 6.0, 80.0], (4, 10): [5.0, 80.0, 80.0], (5, 10): [80.0] * 3})
    summary = installation_summary(snapshots).set_index("unit_id")["days_marked"]
    assert summary.to_dict() == {3: 2, 4: 1}


def test_the_real_manual_list_marks_units_5337_and_5515_until_the_probe_is_buried():
    # Sondas planas en 15 a 17 que saltan a 87 y 91 de golpe: no son reposo, son sondas sin enterrar.
    flat_5337 = make_snapshots({(5337, 10): [16.0] * 70 + [87.0, 82.0]}, start="2026-04-22")
    flat_5515 = make_snapshots({(5515, 15): [16.0] * 70 + [91.0, 92.0]}, start="2026-07-23")
    snapshots = pd.concat([flat_5337, flat_5515], ignore_index=True)
    days = installation_days(snapshots)  # usa MANUAL_INSTALLATION
    for unit, last_marked, first_good in ((5337, "2026-06-27", "2026-06-28"), (5515, "2026-09-27", "2026-09-28")):
        marked = days[days["unit_id"] == unit]["day"]
        assert day(last_marked) in set(marked)
        assert day(first_good) not in set(marked)
    assert (days[days["unit_id"] == 5337]["day"] >= day("2026-04-22")).all()
    assert len(days[days["unit_id"] == 5337]) == 67 and len(days[days["unit_id"] == 5515]) == 67


# ---------- Días con lecturas SMMAdmin ----------

def make_daily(rows):
    """`rows`: (unit, 'AAAA-MM-DD', profundidad, n_admin) -> el formato de daily.parquet que usa admin_days."""
    return pd.DataFrame(
        [(u, pd.Timestamp(d, tz="UTC"), depth, n) for u, d, depth, n in rows],
        columns=["unit_id", "day", "depth_cm", "n_admin"],
    )


def test_admin_days_marks_a_day_with_partial_admin_readings_in_any_depth():
    daily = make_daily(
        [
            (1, "2026-01-01", 15, 96),  # día completo
            (1, "2026-01-01", 55, 96),
            (1, "2026-01-02", 15, 0),  # solo una profundidad y unas pocas lecturas: cuenta
            (1, "2026-01-02", 55, 3),
            (1, "2026-01-03", 15, 0),  # sin SMMAdmin: no cuenta
            (1, "2026-01-03", 55, 0),
        ]
    )
    result = admin_days(daily)
    assert result["day"].tolist() == [day("2026-01-01"), day("2026-01-02")]
    assert set(result["unit_id"]) == {1}


def test_installation_is_the_union_of_automatic_manual_and_admin_rules():
    # Unidad 1: automática (día 1, valor 5). Unidad 2: manual (día 5). Unidad 3: solo SMMAdmin (días 2 y 3).
    snapshots = make_snapshots(
        {(1, 10): [5.0] + [80.0] * 5, (2, 10): [70.0] * 6, (3, 10): [70.0] * 6}
    )
    daily = make_daily([(3, "2026-01-02", 10, 4), (3, "2026-01-03", 10, 96)])
    days = installation_days(snapshots, manual={2: [("2026-01-05", "2026-01-05")]}, admin=admin_days(daily))
    assert set(zip(days["unit_id"], days["day"])) == {
        (1, day("2026-01-01")), (2, day("2026-01-05")), (3, day("2026-01-02")), (3, day("2026-01-03"))
    }


def test_admin_days_overlapping_other_rules_are_not_counted_twice():
    snapshots = make_snapshots({(1, 10): [5.0, 5.0, 80.0, 80.0]})  # automática: días 1 y 2
    daily = make_daily([(1, "2026-01-02", 10, 10), (1, "2026-01-03", 10, 10)])  # SMMAdmin: días 2 y 3
    days = installation_days(snapshots, admin=admin_days(daily))
    assert days["day"].tolist() == [day("2026-01-01"), day("2026-01-02"), day("2026-01-03")]


def test_a_unit_without_admin_readings_and_unknown_units_change_nothing():
    snapshots = make_snapshots({(1, 10): [70.0] * 4})
    no_admin = make_daily([(1, "2026-01-01", 10, 0), (1, "2026-01-02", 10, 0)])
    assert installation_days(snapshots, admin=admin_days(no_admin)).empty
    other_unit = make_daily([(99, "2026-01-01", 10, 5)])  # una unidad que no está en las fotos
    assert installation_days(snapshots, admin=admin_days(other_unit)).empty
    assert installation_days(snapshots, admin=None).empty


def test_admin_dates_with_another_time_resolution_still_match_the_snapshots():
    snapshots = make_snapshots({(1, 10): [70.0] * 4})
    daily = make_daily([(1, "2026-01-02", 10, 1)])
    daily["day"] = daily["day"].astype("datetime64[us, UTC]")
    snapshots["day"] = snapshots["day"].astype("datetime64[ns, UTC]")
    days = installation_days(snapshots, admin=admin_days(daily))
    assert installation_mask(snapshots, days).tolist() == [False, True, False, False]


# ---------- Lluvia del intervalo (2.5) ----------

def test_interval_rain_takes_the_max_of_previous_day_and_same_day_at_lead_zero():
    weather = pd.DataFrame(
        {
            "location_id": ["a", "a", "a"],
            "day": [day("2026-01-01"), day("2026-01-01"), day("2026-01-02")],
            "lead_days": [0, 3, 0],
            "precipitation_mm": [4.0, 9.0, 1.0],
        }
    )
    units = pd.DataFrame({"unit_id": [1, 2], "location_id": ["a", "a"]})
    rain = interval_rain(weather, units).set_index(["unit_id", "day"])["rain_mm"]
    assert rain[(1, day("2026-01-01"))] == 4.0  # solo se conoce el propio día (d−1 sin dato)
    assert rain[(1, day("2026-01-02"))] == 4.0  # max(d−1 = 4, d = 1)
    assert rain[(2, day("2026-01-03"))] == 1.0  # solo se conoce d−1
    assert len(rain) == 6  # tres días por unidad; la antelación 3 no cuenta


def test_rain_on_the_same_day_alone_discards_a_rise():
    """La lluvia de madrugada del propio día d sube el índice sin ser riego."""
    snapshots = make_snapshots({(1, 10): [50.0, 50.0, 56.0, 56.0]})
    weather = pd.DataFrame(
        {
            "location_id": ["a", "a"],
            "day": [day("2026-01-02"), day("2026-01-03")],
            "lead_days": [0, 0],
            "precipitation_mm": [0.0, 3.0],  # seco el 2, llueve el 3 (el día de la subida)
        }
    )
    rain = interval_rain(weather, pd.DataFrame({"unit_id": [1], "location_id": ["a"]}))
    marks = possible_irrigation(snapshots, rain).set_index("day")["possible_irrigation"]
    assert not marks[day("2026-01-03")]
    # Con la regla anterior (solo d−1 = 0 mm) se habría marcado como riego
    assert possible_irrigation(snapshots, rain.assign(rain_mm=0.0)).set_index("day").loc[day("2026-01-03"), "possible_irrigation"]


def test_rain_below_the_limit_on_both_days_does_not_discard():
    snapshots = make_snapshots({(1, 10): [50.0, 50.0, 56.0, 56.0]})
    weather = pd.DataFrame(
        {
            "location_id": ["a", "a"],
            "day": [day("2026-01-02"), day("2026-01-03")],
            "lead_days": [0, 0],
            "precipitation_mm": [1.9, 1.9],
        }
    )
    rain = interval_rain(weather, pd.DataFrame({"unit_id": [1], "location_id": ["a"]}))
    marks = possible_irrigation(snapshots, rain).set_index("day")["possible_irrigation"]
    assert marks[day("2026-01-03")]


def test_rain_from_real_format_discards_a_rise():
    snapshots = make_snapshots({(1, 10): [50.0, 50.0, 56.0, 56.0]})
    weather = pd.DataFrame(
        {"location_id": ["a"], "day": [day("2026-01-02")], "lead_days": [0], "precipitation_mm": [8.0]}
    )
    rain = interval_rain(weather, pd.DataFrame({"unit_id": [1], "location_id": ["a"]}))
    marks = possible_irrigation(snapshots, rain).set_index("day")["possible_irrigation"]
    assert not marks[day("2026-01-03")]  # llovió 8 mm el día 2: la subida del 3 se atribuye a la lluvia
