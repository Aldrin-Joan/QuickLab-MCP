"""Catalog seed definitions for all 7 approved tabular machine learning models."""

import hashlib

from ml_mcp.domain.value_objects import (
    ApprovalStatus,
    HyperparameterConstraint,
    ModelFamily,
    ModelMetadata,
    TaskType,
)


def _make_digest(model_id: str) -> str:
    """Generate deterministic SHA-256 container image digest for model family."""
    return f"sha256:{hashlib.sha256(f'modellab/trainer-{model_id}:1.0.0'.encode()).hexdigest()}"


APPROVED_MODELS: list[ModelMetadata] = [
    # 1. Logistic Regression
    ModelMetadata(
        model_id="logistic_regression",
        version="1.0.0",
        name="Logistic Regression",
        description="Regularized linear model for binary and multiclass classification",
        family=ModelFamily.LOGISTIC_REGRESSION,
        supported_task_types=[TaskType.BINARY_CLASSIFICATION, TaskType.MULTICLASS_CLASSIFICATION],
        approval_status=ApprovalStatus.APPROVED,
        container_image_digest=_make_digest("logistic_regression"),
        supported_hyperparameters=[
            HyperparameterConstraint(name="C", type="float", default=1.0, min_value=0.0001, max_value=10000.0, description="Inverse of regularization strength"),
            HyperparameterConstraint(name="penalty", type="str", default="l2", allowed_values=["l1", "l2", "elasticnet", "none"], description="Norm used in penalization"),
            HyperparameterConstraint(name="max_iter", type="int", default=200, min_value=10, max_value=5000, description="Maximum iterations for solver convergence"),
        ],
    ),
    # 2. Random Forest
    ModelMetadata(
        model_id="random_forest",
        version="1.0.0",
        name="Random Forest",
        description="Ensemble of decision trees trained with bagging and feature subsampling",
        family=ModelFamily.RANDOM_FOREST,
        supported_task_types=[TaskType.BINARY_CLASSIFICATION, TaskType.MULTICLASS_CLASSIFICATION, TaskType.REGRESSION],
        approval_status=ApprovalStatus.APPROVED,
        container_image_digest=_make_digest("random_forest"),
        supported_hyperparameters=[
            HyperparameterConstraint(name="n_estimators", type="int", default=100, min_value=10, max_value=1000, description="Number of trees in the forest"),
            HyperparameterConstraint(name="max_depth", type="int", default=10, min_value=1, max_value=50, description="Maximum tree depth"),
            HyperparameterConstraint(name="min_samples_split", type="int", default=2, min_value=2, max_value=20, description="Minimum samples to split an internal node"),
        ],
    ),
    # 3. XGBoost
    ModelMetadata(
        model_id="xgboost",
        version="1.0.0",
        name="XGBoost",
        description="Extreme Gradient Boosting decision trees optimized for speed and performance",
        family=ModelFamily.XGBOOST,
        supported_task_types=[TaskType.BINARY_CLASSIFICATION, TaskType.MULTICLASS_CLASSIFICATION, TaskType.REGRESSION],
        approval_status=ApprovalStatus.APPROVED,
        container_image_digest=_make_digest("xgboost"),
        supported_hyperparameters=[
            HyperparameterConstraint(name="n_estimators", type="int", default=100, min_value=10, max_value=2000, description="Number of boosting rounds"),
            HyperparameterConstraint(name="max_depth", type="int", default=6, min_value=1, max_value=20, description="Maximum tree depth"),
            HyperparameterConstraint(name="learning_rate", type="float", default=0.1, min_value=0.001, max_value=1.0, description="Step size shrinkage"),
            HyperparameterConstraint(name="subsample", type="float", default=1.0, min_value=0.1, max_value=1.0, description="Subsample ratio of the training instances"),
        ],
    ),
    # 4. LightGBM
    ModelMetadata(
        model_id="lightgbm",
        version="1.0.0",
        name="LightGBM",
        description="Fast, distributed, high-performance gradient boosting based on decision tree algorithms",
        family=ModelFamily.LIGHTGBM,
        supported_task_types=[TaskType.BINARY_CLASSIFICATION, TaskType.MULTICLASS_CLASSIFICATION, TaskType.REGRESSION],
        approval_status=ApprovalStatus.APPROVED,
        container_image_digest=_make_digest("lightgbm"),
        supported_hyperparameters=[
            HyperparameterConstraint(name="n_estimators", type="int", default=100, min_value=10, max_value=2000, description="Number of boosting iterations"),
            HyperparameterConstraint(name="num_leaves", type="int", default=31, min_value=2, max_value=256, description="Max tree leaves"),
            HyperparameterConstraint(name="learning_rate", type="float", default=0.1, min_value=0.001, max_value=1.0, description="Boosting learning rate"),
        ],
    ),
    # 5. CatBoost
    ModelMetadata(
        model_id="catboost",
        version="1.0.0",
        name="CatBoost",
        description="Gradient boosting on decision trees with native categorical feature support",
        family=ModelFamily.CATBOOST,
        supported_task_types=[TaskType.BINARY_CLASSIFICATION, TaskType.MULTICLASS_CLASSIFICATION, TaskType.REGRESSION],
        approval_status=ApprovalStatus.APPROVED,
        container_image_digest=_make_digest("catboost"),
        supported_hyperparameters=[
            HyperparameterConstraint(name="iterations", type="int", default=100, min_value=10, max_value=2000, description="Number of boosting iterations"),
            HyperparameterConstraint(name="depth", type="int", default=6, min_value=1, max_value=16, description="Depth of the trees"),
            HyperparameterConstraint(name="learning_rate", type="float", default=0.1, min_value=0.001, max_value=1.0, description="Learning rate"),
        ],
    ),
    # 6. Linear SVM
    ModelMetadata(
        model_id="linear_svm",
        version="1.0.0",
        name="Linear Support Vector Machine",
        description="Linear support vector machine classifier and regressor. Note: LinearSVC uses decision boundaries without native probability estimates, so probability-dependent metrics (roc_auc, pr_auc, log_loss, brier_score) are omitted.",
        family=ModelFamily.LINEAR_SVM,
        supported_task_types=[TaskType.BINARY_CLASSIFICATION, TaskType.MULTICLASS_CLASSIFICATION, TaskType.REGRESSION],
        approval_status=ApprovalStatus.APPROVED,
        container_image_digest=_make_digest("linear_svm"),
        supported_hyperparameters=[
            HyperparameterConstraint(name="C", type="float", default=1.0, min_value=0.001, max_value=1000.0, description="Regularization parameter"),
            HyperparameterConstraint(name="max_iter", type="int", default=1000, min_value=100, max_value=10000, description="Max iterations"),
        ],
    ),
    # 7. MLP (Multi-Layer Perceptron)
    ModelMetadata(
        model_id="mlp",
        version="1.0.0",
        name="Multi-Layer Perceptron",
        description="Feedforward artificial neural network with backpropagation training",
        family=ModelFamily.MLP,
        supported_task_types=[TaskType.BINARY_CLASSIFICATION, TaskType.MULTICLASS_CLASSIFICATION, TaskType.REGRESSION],
        approval_status=ApprovalStatus.APPROVED,
        container_image_digest=_make_digest("mlp"),
        supported_hyperparameters=[
            HyperparameterConstraint(name="alpha", type="float", default=0.0001, min_value=0.000001, max_value=1.0, description="L2 penalty regularization"),
            HyperparameterConstraint(name="max_iter", type="int", default=200, min_value=50, max_value=2000, description="Maximum iterations"),
        ],
    ),
]
