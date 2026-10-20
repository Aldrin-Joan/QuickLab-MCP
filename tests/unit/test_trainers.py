"""Unit tests verifying all 7 approved tabular model trainers."""

import numpy as np
import pytest

from ml_mcp.domain.value_objects import ModelFamily, TaskType
from ml_mcp.workers.trainers import get_trainer


@pytest.fixture
def synthetic_classification_data():
    np.random.seed(42)
    X = np.random.randn(100, 5)
    # Simple linear decision boundary
    y = (X[:, 0] + X[:, 1] > 0).astype(int)
    return X[:80], y[:80], X[80:], y[80:]


@pytest.fixture
def synthetic_regression_data():
    np.random.seed(42)
    X = np.random.randn(100, 5)
    y = X[:, 0] * 2.0 + X[:, 1] * -1.5 + np.random.randn(100) * 0.1
    return X[:80], y[:80], X[80:], y[80:]


@pytest.mark.parametrize(
    "family",
    [
        ModelFamily.LOGISTIC_REGRESSION,
        ModelFamily.RANDOM_FOREST,
        ModelFamily.XGBOOST,
        ModelFamily.LIGHTGBM,
        ModelFamily.CATBOOST,
        ModelFamily.LINEAR_SVM,
        ModelFamily.MLP,
    ],
)
def test_all_classification_trainers(family: ModelFamily, synthetic_classification_data):
    X_tr, y_tr, X_val, y_val = synthetic_classification_data
    trainer = get_trainer(family)

    result = trainer.train(
        X_tr=X_tr,
        y_tr=y_tr,
        X_val=X_val,
        y_val=y_val,
        task_type=TaskType.BINARY_CLASSIFICATION,
        hyperparameters={},
        random_seed=42,
    )

    assert result.val_predictions.shape[0] == 20
    assert "accuracy" in result.validation_metrics
    assert result.validation_metrics["accuracy"] > 0.5
    assert len(result.model_artifact_bytes) > 0


@pytest.mark.parametrize(
    "family",
    [
        ModelFamily.RANDOM_FOREST,
        ModelFamily.XGBOOST,
        ModelFamily.LIGHTGBM,
        ModelFamily.CATBOOST,
        ModelFamily.LINEAR_SVM,
        ModelFamily.MLP,
    ],
)
def test_regression_trainers(family: ModelFamily, synthetic_regression_data):
    X_tr, y_tr, X_val, y_val = synthetic_regression_data
    trainer = get_trainer(family)

    result = trainer.train(
        X_tr=X_tr,
        y_tr=y_tr,
        X_val=X_val,
        y_val=y_val,
        task_type=TaskType.REGRESSION,
        hyperparameters={},
        random_seed=42,
    )

    assert result.val_predictions.shape[0] == 20
    assert "r2" in result.validation_metrics
    assert "mse" in result.validation_metrics
    assert len(result.model_artifact_bytes) > 0


def test_catboost_trainer_categorical_and_no_disk_files(tmp_path, monkeypatch):
    """Verify CatBoost trains without creating catboost_info directory on disk."""
    import os

    import pandas as pd

    from ml_mcp.workers.preprocessing import PreprocessingPipeline

    # Change working dir to empty tmp_path to verify no catboost_info is created
    monkeypatch.chdir(tmp_path)

    df = pd.DataFrame(
        {
            "num_1": [1.0, 2.5, 3.2, 4.8, 5.1, 6.0, 7.3, 8.9] * 10,
            "cat_feat": ["A", "B", "C", "A", "B", "C", "A", "B"] * 10,
            "target": [0, 1, 0, 1, 0, 1, 0, 1] * 10,
        }
    )

    pipeline = PreprocessingPipeline(
        target_column="target",
        task_type=TaskType.BINARY_CLASSIFICATION,
    )
    folds = pipeline.fit_transform_folds(df)
    X_tr, X_val, y_tr, y_val, _ = folds[0]

    trainer = get_trainer(ModelFamily.CATBOOST)
    result = trainer.train(
        X_tr=X_tr,
        y_tr=y_tr,
        X_val=X_val,
        y_val=y_val,
        task_type=TaskType.BINARY_CLASSIFICATION,
        hyperparameters={"iterations": 10},
    )

    assert result.val_predictions is not None
    assert "accuracy" in result.validation_metrics
    # Verify no catboost_info directory was created anywhere in tmp_path
    assert not os.path.exists(tmp_path / "catboost_info")
    assert not os.path.exists("catboost_info")
