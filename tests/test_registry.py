"""Registro de modelos (tarea 4.3): construcción de los registros y de las métricas C4, y ml_model en PostgreSQL."""

import datetime as dt
import json

import pandas as pd
import pytest

from irrigation_app.models import registry
from irrigation_app.models.training import FEATURE_COLUMNS


def metrics_frame():
    rows = []
    for scenario in ("all", "no_shared", "no_shared_retrained"):
        for split in ("validation", "test"):
            for horizon in range(1, 8):
                for method, mae in (("persistence", 1.0 + horizon), ("lightgbm", 0.9 + horizon), ("ridge", 1.1 + horizon)):
                    rows.append((scenario, split, method, horizon, 100, mae, mae * 2, 0.1, 5.0, 4.0))
    columns = ["scenario", "split", "method", "horizon_days", "n", "mae", "rmse", "bias", "mae_improvement_pct", "rmse_improvement_pct"]
    return pd.DataFrame(rows, columns=columns)


def summary():
    chosen = {str(h): {"params": {"num_leaves": 63}, "best_iteration": 18, "valid_mae": 0.8} for h in range(1, 8)}
    return {"generated_at": "2026-11-05T13:27:19Z", "versions": {"lightgbm": "4.7.0"}, "chosen": {"all": {"lightgbm": chosen}}}


def test_build_metrics_has_model_and_persistence_by_scenario_and_split():
    metrics = registry.build_metrics(metrics_frame(), "lightgbm", 3)
    test_all = metrics["scenarios"]["all"]["test"]
    assert test_all["mae"] == pytest.approx(3.9) and test_all["persistence"]["mae"] == pytest.approx(4.0)
    assert set(metrics["scenarios"]) == {"all", "no_shared"}  # el reentrenado sin shared no entra
    assert metrics["typical_error"] == {"split": "test", "scenario": "all", "n": 100, "mae": pytest.approx(3.9), "rmse": pytest.approx(7.8), "bias": pytest.approx(0.1)}
    json.dumps(metrics, allow_nan=False)  # válido como JSON: sin NaN


def test_nan_values_become_null_in_the_metrics():
    frame = metrics_frame()
    frame.loc[(frame["method"] == "lightgbm") & (frame["horizon_days"] == 1), "bias"] = float("nan")
    metrics = registry.build_metrics(frame, "lightgbm", 1)
    assert metrics["scenarios"]["all"]["test"]["bias"] is None
    json.dumps(metrics, allow_nan=False)


def test_build_records_one_per_horizon_with_artifact_and_hyperparameters(tmp_path):
    ranges = {h: (dt.date(2024, 6, 1), dt.date(2026, 7, 31 - h)) for h in range(1, 8)}
    records = registry.build_records(summary(), metrics_frame(), models_dir=tmp_path, train_ranges=ranges, code_version="git:abc")
    assert [r.horizon_days for r in records] == list(range(1, 8))
    first = records[0]
    assert first.algorithm == "lightgbm" and first.features["columns"] == FEATURE_COLUMNS
    assert first.trained_at == dt.datetime(2026, 11, 5, 13, 27, 19, tzinfo=dt.timezone.utc)
    assert (first.train_from, first.train_to) == (dt.date(2024, 6, 1), dt.date(2026, 7, 30))
    assert first.hyperparameters["params"] == {"num_leaves": 63}
    assert first.artifact_path.endswith("lightgbm_h1.joblib") and first.code_version == "git:abc"
    assert first.metrics["typical_error"]["mae"] == pytest.approx(1.9)


def test_unknown_model_in_summary_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="ridge"):
        registry.build_records(summary(), metrics_frame(), algorithms=("ridge",), models_dir=tmp_path)


def test_persistence_record_has_no_artifact(tmp_path):
    record = registry.build_records(summary(), metrics_frame(), algorithms=("persistence",), models_dir=tmp_path)[0]
    assert record.artifact_path is None and record.features["columns"] == []


def test_training_date_ranges_use_only_training_rows():
    table = pd.DataFrame(
        {"horizon_days": [1, 1, 1], "origin_day": pd.to_datetime(["2025-01-01", "2025-03-01", "2026-09-01"], utc=True)}
    )
    labels = pd.Series(["train", "train", "test"])
    assert registry.training_date_ranges(table, labels) == {1: (dt.date(2025, 1, 1), dt.date(2025, 3, 1))}


def test_artifacts_outside_the_data_folder_are_refused():
    with pytest.raises(ValueError, match="data/"):
        registry.resolve_artifact("../../etc/modelo.joblib")


# --- Con PostgreSQL local ------------------------------------------------------------------------

def record(horizon=1, algorithm="lightgbm", trained_at=None):
    return registry.ModelRecord(
        algorithm=algorithm, horizon_days=horizon, trained_at=trained_at or dt.datetime(2026, 11, 5, tzinfo=dt.timezone.utc),
        data_source="agrodevices", moisture_unit="relative_index_0_100", features={"columns": ["a"]},
        train_from=dt.date(2024, 6, 1), train_to=dt.date(2026, 7, 30), hyperparameters={"params": {}},
        metrics={"typical_error": {"mae": 0.7}}, artifact_path=f"data/ml/models/{algorithm}_h{horizon}.joblib", code_version="git:abc",
    )


@pytest.mark.integration
def test_register_is_idempotent_and_stores_the_json(pg_connection):
    first = registry.register_model(pg_connection, record())
    again = registry.register_model(pg_connection, record())
    assert first == again
    assert pg_connection.execute("SELECT count(*) FROM ml_model").fetchone()[0] == 1
    metrics, features = pg_connection.execute("SELECT metrics, features FROM ml_model").fetchone()
    assert metrics["typical_error"]["mae"] == 0.7 and features == {"columns": ["a"]}


@pytest.mark.integration
def test_activating_a_model_deactivates_the_previous_one_of_the_same_horizon(pg_connection):
    old = registry.register_model(pg_connection, record(trained_at=dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)), activate=True)
    other_horizon = registry.register_model(pg_connection, record(horizon=2), activate=True)
    new = registry.register_model(pg_connection, record(trained_at=dt.datetime(2026, 11, 5, tzinfo=dt.timezone.utc)), activate=True)
    active = registry.active_models(pg_connection)
    assert active[1]["model_id"] == new and active[2]["model_id"] == other_horizon
    assert pg_connection.execute("SELECT is_active FROM ml_model WHERE model_id = %s", (old,)).fetchone()[0] is False
    assert pg_connection.execute("SELECT count(*) FROM ml_model WHERE is_active AND horizon_days = 1").fetchone()[0] == 1


@pytest.mark.integration
def test_activating_an_unknown_model_fails(pg_connection):
    with pytest.raises(ValueError):
        registry.activate_model(pg_connection, 12345)


def test_training_depths_are_stored_in_features_when_given(tmp_path):
    with_depths = registry.build_records(summary(), metrics_frame(), models_dir=tmp_path, training_depths=[35, 15, 25])
    assert with_depths[0].features["training_depths_cm"] == [15, 25, 35]
    without = registry.build_records(summary(), metrics_frame(), models_dir=tmp_path)
    assert "training_depths_cm" not in without[0].features


def test_depths_are_read_back_from_features():
    assert registry._depths({"training_depths_cm": [15, 25]}) == (15, 25)
    assert registry._depths({"columns": []}) is None and registry._depths(None) is None
