"""Pruebas de los modelos, la evaluación y el experimento con datos sintéticos (tareas 3.2 a 3.4)."""

import numpy as np
import pandas as pd
import pytest

from irrigation_app.models.evaluation import error_metrics, evaluate_methods, improvement_pct
from irrigation_app.models.experiment import ALL, NO_SHARED, NO_SHARED_RETRAINED, run_experiment
from irrigation_app.models.training import (
    FEATURE_COLUMNS,
    PersistenceModel,
    make_ridge,
    predict_change,
    predict_level,
    search_model,
)

SMALL_GRIDS = {
    "ridge": [{"alpha": 1.0}, {"alpha": 10.0}],
    "lightgbm": [{"num_leaves": 7, "min_child_samples": 20, "learning_rate": 0.1, "n_estimators": 40}],
}


def synthetic_table(n: int = 600, seed: int = 0, horizons=(1, 2)) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frames = []
    for horizon in horizons:
        base = rng.uniform(40, 80, n)
        et0 = rng.uniform(0, 6, n)
        rain = rng.uniform(0, 10, n)
        change = -0.3 * et0 + 0.2 * rain + 0.02 * (60 - base) + rng.normal(0, 0.2, n)
        frames.append(
            pd.DataFrame(
                {
                    "unit_id": rng.integers(1, 11, n),
                    "depth_cm": rng.choice([10, 20, 30], n).astype("uint16"),
                    "horizon_days": horizon,
                    "base_index": base,
                    "change_1d": np.where(rng.random(n) < 0.1, np.nan, rng.normal(0, 1, n)),
                    "change_3d": np.where(rng.random(n) < 0.1, np.nan, rng.normal(0, 1, n)),
                    "doy_sin": rng.uniform(-1, 1, n),
                    "doy_cos": rng.uniform(-1, 1, n),
                    "precip_sum_mm": rain,
                    "et0_sum_mm": et0,
                    "temp_mean_c": rng.uniform(5, 30, n),
                    "humidity_mean_pct": rng.uniform(20, 90, n),
                    "wind_mean_kmh": rng.uniform(0, 20, n),
                    "target_change": change,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def labels_for(table: pd.DataFrame) -> pd.Series:
    position = table.groupby("horizon_days").cumcount()
    n = position.max() + 1
    values = np.where(position < 0.5 * n, "train", np.where(position < 0.7 * n, "validation", "test"))
    return pd.Series(values, index=table.index, dtype="object")


def test_persistence_returns_origin():
    table = synthetic_table(20)
    change = predict_change(PersistenceModel(), table)
    assert (change == 0).all()
    assert np.allclose(predict_level(table["base_index"], change), table["base_index"])


def test_level_is_origin_plus_change():
    assert predict_level([70.0], [-2.5])[0] == pytest.approx(67.5)


def test_ridge_handles_nulls_and_adds_indicators():
    table = synthetic_table(200)
    features = table[FEATURE_COLUMNS].astype(float)
    model = make_ridge(1.0).fit(features, table["target_change"])
    assert np.isfinite(model.predict(features)).all()
    # Una columna indicadora por cada variable con nulos (change_1d y change_3d).
    assert model.named_steps["impute"].indicator_.features_.tolist() == [2, 3]


def test_same_seed_same_result():
    table = synthetic_table(400)
    train, valid = table.iloc[:250], table.iloc[250:]
    args = (FEATURE_COLUMNS, SMALL_GRIDS["lightgbm"])
    first = search_model("lightgbm", train, valid, *args, seed=7, n_jobs=1)
    second = search_model("lightgbm", train, valid, *args, seed=7, n_jobs=1)
    assert first.best.valid_mae == second.best.valid_mae
    assert np.array_equal(predict_change(first.best.model, valid), predict_change(second.best.model, valid))


def test_search_picks_lowest_validation_mae():
    table = synthetic_table(400)
    result = search_model("ridge", table.iloc[:250], table.iloc[250:], FEATURE_COLUMNS, [{"alpha": 1.0}, {"alpha": 1e9}])
    assert result.best.params == {"alpha": 1.0}
    assert len(result.candidates) == 2


def test_error_metrics_known_values():
    metrics = error_metrics(np.array([1.0, 2.0, 3.0]), np.array([2.0, 2.0, 1.0]))
    # errores (previsto - real): +1, 0, -2
    assert metrics["n"] == 3
    assert metrics["mae"] == pytest.approx(1.0)
    assert metrics["rmse"] == pytest.approx(np.sqrt(5 / 3))
    assert metrics["bias"] == pytest.approx(-1 / 3)


def test_improvement_against_persistence():
    assert improvement_pct(2.0, 1.5) == pytest.approx(25.0)
    assert improvement_pct(2.0, 2.5) == pytest.approx(-25.0)
    actual = np.array([1.0, -1.0, 2.0, -2.0])
    frame = evaluate_methods(3, actual, {"persistence": np.zeros(4), "perfect": actual})
    perfect = frame[frame["method"] == "perfect"].iloc[0]
    assert perfect["mae"] == 0 and perfect["mae_improvement_pct"] == pytest.approx(100.0)
    base = frame[frame["method"] == "persistence"].iloc[0]
    assert base["mae"] == pytest.approx(1.5) and base["mae_improvement_pct"] == 0


def test_evaluate_requires_persistence():
    with pytest.raises(ValueError):
        evaluate_methods(1, np.zeros(2), {"ridge": np.zeros(2)})


def test_experiment_shapes_and_scenarios():
    table = synthetic_table(500)
    result = run_experiment(
        table, labels_for(table), [1, 2], horizons=[1, 2], grids=SMALL_GRIDS, seed=3, n_jobs=1, log=lambda _: None
    )
    metrics = result["metrics"]
    test = metrics[(metrics["scenario"] == ALL) & (metrics["split"] == "test")]
    assert len(test[test["method"].isin(["persistence", "ridge", "lightgbm"])]) == 3 * 2  # 3 métodos x 2 horizontes
    assert set(metrics["scenario"]) == {ALL, NO_SHARED, NO_SHARED_RETRAINED}
    # Sin shared hay menos ejemplos que con todas las unidades.
    def n_examples(scenario):
        rows = metrics[(metrics["scenario"] == scenario) & (metrics["split"] == "test") & (metrics["method"] == "persistence")]
        return rows["n"].sum()
    assert n_examples(NO_SHARED) < n_examples(ALL)
    # Con una señal clara en la meteorología, LightGBM bate a la persistencia y mejora al no ver el tiempo.
    mae = test.set_index(["method", "horizon_days"])["mae"]
    assert mae[("lightgbm", 1)] < mae[("persistence", 1)]
    assert mae[("lightgbm", 1)] < mae[("lightgbm_no_weather", 1)]


def test_experiment_is_reproducible():
    table = synthetic_table(300)
    args = dict(horizons=[1], grids=SMALL_GRIDS, seed=5, n_jobs=1, log=lambda _: None)
    first = run_experiment(table, labels_for(table), [1], **args)["metrics"]
    second = run_experiment(table, labels_for(table), [1], **args)["metrics"]
    pd.testing.assert_frame_equal(first, second)
