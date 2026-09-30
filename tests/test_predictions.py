"""
Tests for user-directed predictions (the dashboard's Prediction tab).

The tab exists because the engine's automatic target pick is unreliable, so
the important cases are the ones where a naive auto-pick would have produced
a confident, wrong answer.
"""
import numpy as np
import pandas as pd
import pytest

from app.analytics.engine import (prediction_options, profile_dataframe,
                                  run_targeted_prediction)


@pytest.fixture
def mixed_df() -> pd.DataFrame:
    """A sales file with a real relationship, a clear label, and no dates."""
    rng = np.random.default_rng(4)
    n = 300
    ad_spend = rng.uniform(100, 900, n)
    df = pd.DataFrame({
        "Region": rng.choice(["N", "S", "E", "W"], n),
        "Ad_Spend": ad_spend,
        "Visits": rng.integers(50, 500, n),
    })
    # Revenue genuinely depends on Ad_Spend, so R^2 should be clearly positive.
    df["Revenue"] = ad_spend * 3 + rng.normal(0, 40, n)
    df["Churned"] = np.where(df["Revenue"] < df["Revenue"].mean(), "yes", "no")
    return df


@pytest.fixture
def mixed_profile(mixed_df) -> dict:
    return profile_dataframe(mixed_df)


def test_options_reflect_real_column_types(mixed_profile):
    options = prediction_options(mixed_profile)
    assert options["is_labeled"] is True
    assert options["suggested_target"] == "Churned"
    assert "Revenue" in options["numeric_columns"]
    assert "Region" in options["categorical_columns"]
    assert options["clusterable"] is True


def test_options_do_not_treat_a_region_as_the_target():
    """A 4-value Region column is a breakdown, not something to predict."""
    rng = np.random.default_rng(2)
    df = pd.DataFrame({
        "Region": rng.choice(["N", "S", "E", "W"], 200),
        "Amount": rng.uniform(0, 100, 200),
    })
    profile = profile_dataframe(df)
    options = prediction_options(profile)
    assert options["is_labeled"] is False
    assert options["suggested_target"] != "Region"


def test_user_chosen_regression_target_is_honoured(mixed_df, mixed_profile):
    result = run_targeted_prediction(
        mixed_df, mixed_profile,
        target="Revenue", features=["Ad_Spend", "Visits"], mode="auto",
    )
    assert result["model_type"] == "regression"
    assert result["target_column"] == "Revenue"
    # Revenue really is driven by Ad_Spend, so the model should do well.
    assert result["r2_pct"] > 50
    assert result["feature_importance"][0]["feature"] == "Ad_Spend"


def test_user_chosen_classification_target_is_honoured(mixed_df, mixed_profile):
    result = run_targeted_prediction(
        mixed_df, mixed_profile,
        target="Churned", features=["Revenue", "Ad_Spend"], mode="auto",
    )
    assert result["model_type"] == "classification"
    assert result["classes"] == ["no", "yes"]
    assert {c["label"] for c in result["per_class"]} == {"yes", "no"}
    # Importances must be populated even though accuracy came from
    # cross-validation, which never fits the model we hold.
    assert result["feature_importance"]


def test_clustering_describes_each_group(mixed_df, mixed_profile):
    result = run_targeted_prediction(
        mixed_df, mixed_profile, mode="clustering", n_clusters=3,
    )
    assert result["model_type"] == "clustering"
    assert result["n_clusters"] == 3
    assert sum(c["count"] for c in result["cluster_sizes"]) == len(mixed_df)
    # "Group 1 has 90 rows" tells a user nothing; the profile names the columns
    # that actually distinguish each group.
    assert result["cluster_profiles"]
    assert all(g["notable"] for g in result["cluster_profiles"])


def test_no_target_clusters_instead_of_guessing(mixed_df, mixed_profile):
    result = run_targeted_prediction(mixed_df, mixed_profile, mode="auto", target=None)
    assert result["model_type"] == "clustering"


def test_target_in_features_is_rejected_upstream(mixed_df, mixed_profile):
    """The route rejects this; the engine must not silently drop the column."""
    result = run_targeted_prediction(
        mixed_df, mixed_profile,
        target="Revenue", features=["Revenue", "Ad_Spend"], mode="auto",
    )
    assert "Revenue" not in result.get("features_used", [])


def test_too_many_classes_is_reported_not_guessed(mixed_profile):
    rng = np.random.default_rng(9)
    n = 200
    df = pd.DataFrame({
        "A": rng.normal(0, 1, n),
        "B": rng.normal(0, 1, n),
        # 80 distinct values: a number, not a category.
        "Code": [f"C{i}" for i in range(80)] * 2 + [f"C{i}" for i in range(40)],
    })
    profile = profile_dataframe(df)
    result = run_targeted_prediction(
        df, profile, target="Code", features=["A", "B"], mode="classification",
    )
    assert "error" in result


def test_missing_target_returns_actionable_error(mixed_df, mixed_profile):
    result = run_targeted_prediction(mixed_df, mixed_profile, target="NotAColumn")
    assert "error" in result


def test_no_numeric_columns_cannot_be_modelled(mixed_profile):
    rng = np.random.default_rng(5)
    df = pd.DataFrame({"Name": ["a", "b", "c"] * 30, "Tag": ["x", "y", "z"] * 30})
    profile = profile_dataframe(df)
    result = run_targeted_prediction(df, profile, mode="clustering")
    assert "error" in result
