import pandas as pd

from app.analytics.engine import (build_ai_fact_sheet, compute_metrics_and_dashboard,
                                   profile_dataframe)


def _sample_df():
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=10).astype(str),
        "region": ["North", "South", "North", "East", "West", "North", "South", "East", "West", "North"],
        "sales": [100, 200, 150, 300, 5000, 120, 220, 310, 90, 130],  # 5000 is an outlier
        "id": range(10),
    })


def test_profile_detects_column_types():
    df = _sample_df()
    profile = profile_dataframe(df)
    assert profile["column_types"]["sales"] == "numeric"
    assert profile["column_types"]["date"] == "date"
    assert profile["column_types"]["region"] == "categorical"
    assert profile["column_types"]["id"] == "identifier"


def test_profile_detects_outliers():
    df = _sample_df()
    profile = profile_dataframe(df)
    sales_col = next(c for c in profile["columns"] if c["name"] == "sales")
    assert sales_col["outlier_count"] >= 1


def test_metrics_match_expected_sum():
    df = _sample_df()
    profile = profile_dataframe(df)
    dashboard = compute_metrics_and_dashboard(df, profile)
    sales_kpi = next(k for k in dashboard["kpi_cards"] if k["metric"] == "sales")
    assert sales_kpi["sum"] == df["sales"].sum()
    assert sales_kpi["count"] == 10


def test_dashboard_includes_time_series_and_category_chart():
    df = _sample_df()
    profile = profile_dataframe(df)
    dashboard = compute_metrics_and_dashboard(df, profile)
    chart_types = {c["type"] for c in dashboard["charts"]}
    assert "line" in chart_types
    assert "bar" in chart_types


def test_fact_sheet_never_contains_raw_rows():
    df = _sample_df()
    profile = profile_dataframe(df)
    dashboard = compute_metrics_and_dashboard(df, profile)
    fact_sheet = build_ai_fact_sheet(profile, dashboard)
    # The fact sheet must only carry aggregates, never a full row/record.
    assert "rows" not in fact_sheet
    assert "kpi_cards" in fact_sheet
