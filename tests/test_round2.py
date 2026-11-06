"""Pruebas de la ronda 2: lluvia y ET0 por día, persistencia con tendencia y métrica de cruce (datos sintéticos)."""

import numpy as np
import pandas as pd
import pytest

from irrigation_app.features import (
    DAILY_FEATURE_COLUMNS,
    add_daily_weather_features,
    daily_weather_columns,
    daily_weather_features,
)
from irrigation_app.models import crossing
from irrigation_app.models.round2 import RIDGE_ALPHAS_WIDE, fit_round2, method_kind_and_features, round2_metrics
from irrigation_app.models.trend import TrendModel, best_slope, fit_drift, fit_trend

MONDAY = pd.Timestamp("2026-03-02", tz="UTC")
UNIT_LOCATIONS = pd.DataFrame({"unit_id": [1], "location_id": ["A"]})


def make_weather(skip=()):
    rows = []
    for lead in range(7):
        for offset in range(7):
            if (offset, lead) in skip:
                continue
            # La lluvia identifica la pareja (día, antelación): 100*lead + offset.
            day = MONDAY + pd.Timedelta(days=offset)
            rows.append(("A", day, lead, 100.0 * lead + offset, 10.0 * lead + offset, 15.0, 50.0, 5.0))
    return pd.DataFrame(
        rows,
        columns=["location_id", "day", "lead_days", "precipitation_mm", "et0_mm", "temperature_c", "humidity_pct", "wind_kmh"],
    )


def examples(horizon):
    return pd.DataFrame({"unit_id": [1], "origin_day": [MONDAY], "horizon_days": [horizon]})


def test_daily_columns_for_a_horizon_only_include_existing_days():
    assert daily_weather_columns(3) == ["precip_d0", "precip_d1", "precip_d2", "et0_d0", "et0_d1", "et0_d2"]
    assert len(DAILY_FEATURE_COLUMNS) == 14


def test_each_day_uses_its_own_lead_time_and_later_days_are_null():
    result = daily_weather_features(examples(3), make_weather(), UNIT_LOCATIONS).iloc[0]
    # día k con antelación k: lluvia 100*k + k, nunca otra antelación
    assert [result[f"precip_d{k}"] for k in range(3)] == [0.0, 101.0, 202.0]
    assert [result[f"et0_d{k}"] for k in range(3)] == [0.0, 11.0, 22.0]
    assert result[["precip_d3", "precip_d6", "et0_d3", "et0_d6"]].isna().all()


def test_horizon_7_fills_every_day():
    result = daily_weather_features(examples(7), make_weather(), UNIT_LOCATIONS).iloc[0]
    assert result[DAILY_FEATURE_COLUMNS].notna().all()
    assert result["precip_d6"] == 606.0


def test_a_missing_day_is_null_and_not_filled():
    result = daily_weather_features(examples(3), make_weather(skip={(1, 1)}), UNIT_LOCATIONS).iloc[0]
    assert np.isnan(result["precip_d1"]) and result["precip_d0"] == 0.0 and result["precip_d2"] == 202.0


def test_add_daily_features_keeps_rows_and_order_for_several_depths():
    table = pd.concat([examples(2).assign(depth_cm=15), examples(2).assign(depth_cm=25)], ignore_index=True)
    merged = add_daily_weather_features(table, make_weather(), UNIT_LOCATIONS)
    assert merged["depth_cm"].tolist() == [15, 25]
    assert merged["precip_d1"].tolist() == [101.0, 101.0]


def test_best_slope_recovers_a_known_trend_and_ignores_missing_rates():
    rate = np.array([-1.0, -0.5, -2.0, np.nan, -1.5])
    actual = 3.0 * rate
    actual[3] = 5.0
    assert best_slope(rate, actual, max_slope=6.0) == pytest.approx(3.0)
    assert best_slope(np.array([np.nan]), np.array([1.0]), 3.0) == 0.0


def test_trend_model_falls_back_to_no_change_without_a_rate():
    frame = pd.DataFrame({"change_1d": [-2.0, np.nan], "change_3d": [np.nan, np.nan]})
    assert TrendModel("change_1d", 2.0).predict(frame).tolist() == [-4.0, 0.0]


def test_fit_trend_picks_the_variant_with_the_lowest_validation_error():
    rng = np.random.default_rng(1)

    def frame(n):
        rate = rng.normal(-1, 0.5, n)
        return pd.DataFrame({"change_1d": rng.normal(0, 3, n), "change_3d": 3 * rate, "target_change": 2 * rate})

    model, summary = fit_trend(frame(300), frame(200), horizon_days=2)
    assert model.source == "change_3d" and summary["chosen"] == "change_3d"
    assert model.slope == pytest.approx(2.0, abs=0.1)


def test_drift_is_the_median_change_of_the_training_set():
    train = pd.DataFrame({"target_change": [-3.0, -1.0, -1.0, 0.5, 10.0]})
    model = fit_drift(train)
    assert model.offset == -1.0
    assert model.predict(train).tolist() == [-1.0] * 5


def test_threshold_matches_the_engine_rule_for_the_reference_plot():
    assert crossing.threshold_index(79.4, 59.7, 0.5) == pytest.approx(69.55)
    with pytest.raises(ValueError):
        crossing.threshold_index(50, 60)


def test_proxy_anchors_use_percentiles_and_refuse_short_series():
    series = pd.Series(np.linspace(40, 80, 100))
    upper, lower = crossing.proxy_anchors(series)
    assert upper == pytest.approx(np.percentile(series, 95)) and lower == pytest.approx(np.percentile(series, 5))
    assert crossing.proxy_anchors(series.iloc[:10]) is None
    assert crossing.proxy_anchors(pd.Series([60.0] * 100)) is None


def test_zone_depths_ignore_missing_and_non_averaged_depths():
    units = pd.DataFrame(
        [{
            "unit_id": 7, "depth_cm_1": 15, "depth_cm_2": 25, "depth_cm_3": pd.NA, "depth_cm_4": 55, "depth_cm_5": pd.NA,
            "in_average_1": True, "in_average_2": True, "in_average_3": True, "in_average_4": False, "in_average_5": False,
        }]
    )
    assert crossing.zone_depths_of(units) == {7: (15, 25)}


def cross_rows(unit_id, origin, base, targets, depths=(15, 25)):
    rows = []
    for depth in depths:
        for h, target in enumerate(targets, start=1):
            rows.append((unit_id, depth, pd.Timestamp(origin, tz="UTC"), h, base, target))
    return pd.DataFrame(rows, columns=["unit_id", "depth_cm", "origin_day", "horizon_days", "base_index", "target_index"])


def test_first_crossing_day_is_zero_without_crossing():
    levels = np.array([[70, 69, 68, 67, 66, 65, 64], [80, 80, 80, 80, 80, 80, 80]], dtype=float)
    assert crossing.first_crossing_day(levels, np.array([67.5, 67.5])).tolist() == [4, 0]


def test_evaluate_crossings_counts_exact_within_one_wrong_omission_and_false_alarm():
    # Umbral 69,55 (anclajes 79,4 y 59,7). Cuatro casos con cruce real el día 3 y dos sin cruce.
    real = [72, 71, 69, 68, 67, 66, 65]
    none = [72] * 7
    cases_spec = [  # (origen, niveles reales, niveles previstos)
        ("2026-08-01", real, [72, 71, 69, 68, 67, 66, 65]),  # día exacto
        ("2026-08-02", real, [72, 71, 71, 69, 67, 66, 65]),  # día 4: dentro de ±1
        ("2026-08-03", real, [72, 72, 72, 72, 72, 72, 66]),  # día 7: día equivocado
        ("2026-08-04", real, none),  # omisión
        ("2026-08-05", none, [72, 72, 69, 69, 69, 69, 69]),  # falsa alarma
        ("2026-08-06", none, none),  # acierto sin cruce
    ]
    frames, changes = [], []
    for origin, actual, predicted in cases_spec:
        frames.append(cross_rows(1, origin, 72.0, actual))
        # Dos profundidades iguales; `cross_rows` ordena por profundidad y luego horizonte.
        changes.append(np.array(predicted * 2, dtype=float) - 72.0)
    rows = pd.concat(frames, ignore_index=True)
    change = np.concatenate(changes)
    cases = crossing.build_cases(rows, {1: (15, 25)}, {"model": change, "persistence": np.zeros(len(rows))})
    result = crossing.evaluate_crossings(cases, {1: 69.55}).set_index("method")
    model = result.loc["model"]
    assert (model["n_cases"], model["n_events"], model["n_no_events"]) == (6, 4, 2)
    assert (model["exact"], model["within_1"], model["wrong_day"], model["omission"], model["false_alarm"]) == (1, 2, 1, 1, 1)
    assert model["exact_pct"] == pytest.approx(25.0) and model["false_alarm_pct"] == pytest.approx(50.0)
    persistence = result.loc["persistence"]
    assert (persistence["omission"], persistence["false_alarm"], persistence["exact"]) == (4, 0, 0)


def test_cases_need_all_horizons_all_depths_and_a_start_above_the_threshold():
    complete = cross_rows(1, "2026-08-01", 72.0, [72] * 7)
    short = cross_rows(1, "2026-08-02", 72.0, [72] * 5)  # faltan horizontes (hubo riego): fuera
    one_depth = cross_rows(1, "2026-08-03", 72.0, [72] * 7, depths=(15,))  # falta una profundidad: fuera
    already_dry = cross_rows(1, "2026-08-04", 60.0, [60] * 7)  # parte por debajo del umbral: no cuenta
    rows = pd.concat([complete, short, one_depth, already_dry], ignore_index=True)
    cases = crossing.build_cases(rows, {1: (15, 25)}, {"persistence": np.zeros(len(rows))})
    assert len(cases["base"]) == 2
    result = crossing.evaluate_crossings(cases, {1: 69.55})
    assert result.loc[0, "n_cases"] == 1  # solo el primero parte por encima del umbral


def test_units_without_threshold_are_skipped():
    rows = cross_rows(1, "2026-08-01", 72.0, [72] * 7)
    cases = crossing.build_cases(rows, {1: (15, 25)}, {"persistence": np.zeros(len(rows))})
    result = crossing.evaluate_crossings(cases, {})
    assert result.loc[0, "n_cases"] == 0 and np.isnan(result.loc[0, "exact_pct"])


def test_zone_mean_by_unit_drops_installation_days_and_days_after_the_cutoff():
    days = pd.date_range("2026-01-01", periods=4, tz="UTC")
    snapshots = pd.DataFrame(
        [(1, d, depth, 50.0 + i) for i, d in enumerate(days) for depth in (15, 25)],
        columns=["unit_id", "day", "depth_cm", "moisture_index"],
    )
    installation = pd.DataFrame({"unit_id": [1], "day": [days[0]]})
    means = crossing.zone_mean_by_unit(snapshots, {1: (15, 25)}, installation, days[2])
    assert means[1].index.tolist() == [days[1], days[2]]
    assert means[1].tolist() == [51.0, 52.0]


def test_ridge_grid_goes_beyond_the_old_edge_of_100():
    assert max(RIDGE_ALPHAS_WIDE) > 100 and 100.0 in RIDGE_ALPHAS_WIDE


def test_models_only_use_daily_columns_that_exist_for_their_horizon():
    _, features = method_kind_and_features("ridge_daily", 2)
    assert "precip_d1" in features and "precip_d2" not in features and "et0_d1" in features
    with pytest.raises(ValueError):
        method_kind_and_features("otro", 1)


def synthetic_round2_table(n=300, horizons=(1, 2), seed=0):
    rng = np.random.default_rng(seed)
    frames = []
    for horizon in horizons:
        rain = rng.uniform(0, 10, (n, 7))
        rate = rng.normal(-0.5, 0.3, n)
        frame = pd.DataFrame(
            {
                "unit_id": rng.integers(1, 6, n), "depth_cm": 15, "horizon_days": horizon,
                "origin_day": pd.Timestamp("2026-03-01", tz="UTC"), "base_index": rng.uniform(50, 80, n),
                "change_1d": rate, "change_3d": rate * 3, "doy_sin": 0.1, "doy_cos": 0.9,
                "precip_sum_mm": rain[:, :horizon].sum(axis=1), "et0_sum_mm": 4.0 * horizon, "temp_mean_c": 20.0,
                "humidity_mean_pct": 50.0, "wind_mean_kmh": 5.0,
            }
        )
        for k in range(7):
            frame[f"precip_d{k}"] = rain[:, k] if k < horizon else np.nan
            frame[f"et0_d{k}"] = 4.0 if k < horizon else np.nan
        frame["target_change"] = horizon * rate + 0.2 * (frame["precip_d0"] - 5.0) + rng.normal(0, 0.1, n)
        frame["split"] = rng.choice(["train", "validation", "test"], n, p=[0.5, 0.25, 0.25])
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def test_round2_pipeline_runs_end_to_end_and_reports_both_reference_improvements(monkeypatch):
    from irrigation_app.models import round2

    monkeypatch.setattr(round2, "RIDGE_WIDE_GRID", [{"alpha": 1.0}, {"alpha": 1000.0}])
    table = synthetic_round2_table()
    fitted = fit_round2(table, [1, 2], log=lambda _: None)
    assert set(fitted["models"]) == {"ridge_wide", "ridge_daily", "lightgbm_daily"}
    metrics = round2_metrics(table, fitted, None, [1, 2], shared_units=[5])
    is_test_all_h1 = (metrics["split"] == "test") & (metrics["scenario"] == "all") & (metrics["horizon_days"] == 1)
    by_method = metrics[is_test_all_h1].set_index("method")
    assert {"persistence", "persistence_trend", "persistence_drift", "ridge_daily", "lightgbm_daily"} <= set(by_method.index)
    assert by_method.loc["persistence", "mae_improvement_pct"] == 0.0
    assert by_method.loc["persistence_trend", "mae_improvement_vs_trend_pct"] == 0.0
    assert by_method.loc["persistence_drift", "mae_improvement_vs_drift_pct"] == 0.0
    # El problema sintético tiene tendencia y lluvia: la tendencia bate a la persistencia y el modelo a las dos.
    assert by_method.loc["persistence_trend", "mae"] < by_method.loc["persistence", "mae"]
    assert by_method.loc["ridge_daily", "mae"] < by_method.loc["persistence_trend", "mae"]
    test_rows = metrics[metrics["split"] == "test"]
    assert test_rows[test_rows["scenario"] == "no_shared"]["n"].max() < test_rows[test_rows["scenario"] == "all"]["n"].max()
