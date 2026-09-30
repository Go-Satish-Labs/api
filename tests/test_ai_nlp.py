"""
Tests for the natural-language question pipeline.

These cover the two failure modes that matter most and are easiest to
regress: mapping a user's wording onto a real column, and reading a trend's
direction correctly (a small per-step slope is a strong compounding trend,
not a flat one).
"""
import numpy as np
import pandas as pd
import pytest

from app.ai.nlp import Aggregate, Intent, parse_question

COLUMNS = ["Date", "Region", "Product", "Units", "Price", "Revenue"]


def test_forecast_intent_and_horizon():
    parsed = parse_question("will sales increase in the next 10 days", COLUMNS)
    assert parsed.intent == Intent.FORECAST
    assert parsed.horizon == 10
    assert parsed.horizon_unit == "days"
    # "sales" is a business word, not a column: it must resolve to Revenue.
    assert parsed.metric == "Revenue"
    assert parsed.direction == "increase"


def test_horizon_unit_is_preserved():
    parsed = parse_question("will revenue go up in the next 2 weeks", COLUMNS)
    assert parsed.horizon == 2
    assert parsed.horizon_unit == "weeks"
    assert parsed.intent == Intent.FORECAST


def test_aggregate_variants():
    assert parse_question("what is the total revenue", COLUMNS).intent == Intent.AGGREGATE
    assert parse_question("what is the total revenue", COLUMNS).aggregate == Aggregate.SUM
    assert parse_question("average units", COLUMNS).aggregate == Aggregate.AVERAGE
    assert parse_question("lowest price", COLUMNS).aggregate == Aggregate.MIN


def test_by_split_assigns_measure_and_group_correctly():
    """'revenue by region' must mean measure=Revenue, group=Region."""
    parsed = parse_question("revenue by region", COLUMNS)
    assert parsed.intent == Intent.COMPARE
    assert parsed.metric == "Revenue"
    assert parsed.group_by == "Region"


def test_dataset_count_vs_column_count():
    dataset_level = parse_question("how many rows", COLUMNS)
    assert dataset_level.intent == Intent.COUNT
    assert dataset_level.metric is None

    column_level = parse_question("how many units", COLUMNS)
    assert column_level.intent == Intent.AGGREGATE
    assert column_level.metric == "Units"
    assert column_level.aggregate == Aggregate.COUNT


def test_other_intents():
    assert parse_question("is there a relationship between price and units", COLUMNS).intent == Intent.CORRELATE
    assert parse_question("what problems does this data have", COLUMNS).intent == Intent.ANOMALY
    assert parse_question("what columns do I have", COLUMNS).intent == Intent.DESCRIBE


def test_unparseable_question_is_not_actionable():
    parsed = parse_question("blueberry pancakes quantum flux", COLUMNS)
    assert parsed.intent == Intent.UNKNOWN
    assert not parsed.is_actionable


def test_empty_question_does_not_raise():
    assert parse_question("", COLUMNS).intent == Intent.UNKNOWN
    assert parse_question(None, COLUMNS).intent == Intent.UNKNOWN


# ── Forecasting ──────────────────────────────────────────────────────────────

def _daily_series(trend_per_day: float, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = 120
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    noise = rng.normal(0, 70, n)
    return pd.DataFrame({
        "Date": dates,
        "Units": np.maximum(1, (1000 + np.arange(n) * trend_per_day + noise) / 20).astype(int),
    })


def test_rising_series_is_reported_as_increasing():
    from app.ai.forecasting import forecast_time_series

    df = _daily_series(trend_per_day=6.5)
    result = forecast_time_series(df, "Date", "Units", periods=10, unit="days")
    assert result is not None
    # A ~0.5%/day slope compounds: reporting this as "flat" would be wrong.
    assert result["direction"] == "increasing"
    assert result["trend_change_pct"] > 3


def test_flat_series_is_reported_as_flat():
    from app.ai.forecasting import forecast_time_series

    df = _daily_series(trend_per_day=0.0, seed=3)
    result = forecast_time_series(df, "Date", "Units", periods=10, unit="days")
    assert result is not None
    assert result["direction"] == "flat"


def test_horizon_converts_through_days():
    from app.ai.forecasting import _periods_per_unit

    assert _periods_per_unit("D", 10, "days") == 10
    assert _periods_per_unit("D", 2, "weeks") == 14     # 14 daily steps
    assert _periods_per_unit("W", 2, "weeks") == 2      # 2 weekly steps
    assert _periods_per_unit("MS", 2, "weeks") == 1     # rounds to one month
    assert _periods_per_unit("D", 1, "months") == 30


def test_forecast_respects_requested_horizon_in_dates():
    from app.ai.forecasting import forecast_time_series

    df = _daily_series(trend_per_day=6.5)
    result = forecast_time_series(df, "Date", "Units", periods=2, unit="weeks")
    assert result is not None
    last = pd.Timestamp(result["last_actual_date"])
    predicted = pd.Timestamp(result["predicted_date"])
    assert (predicted - last).days == 14
    assert len(result["forecast"]) == 14


def test_forecast_refuses_forecast_that_would_go_negative():
    from app.ai.forecasting import forecast_time_series

    rng = np.random.default_rng(1)
    n = 40
    df = pd.DataFrame({
        "Date": pd.date_range("2026-01-01", periods=n, freq="D"),
        "Count": np.linspace(10, 1, n),  # falling toward zero
    })
    result = forecast_time_series(df, "Date", "Count", periods=200, unit="days")
    assert result is not None
    assert all(point["y"] >= 0 for point in result["forecast"])


def test_forecast_returns_none_without_date_column():
    from app.ai.forecasting import forecast_time_series

    df = pd.DataFrame({"Units": [1, 2, 3, 4, 5]})
    assert forecast_time_series(df, "Missing", "Units", 10, "days") is None


def test_no_dates_falls_back_to_holdout_backtest():
    from app.analytics.engine import build_ai_fact_sheet, compute_metrics_and_dashboard, profile_dataframe
    from app.ai.service import answer_question

    rng = np.random.default_rng(11)
    n = 200
    df = pd.DataFrame({
        "A": rng.normal(0, 1, n),
        "B": rng.normal(0, 1, n),
    })
    df["Target"] = df["A"] * 3 + rng.normal(0, 0.3, n)
    profile = profile_dataframe(df)
    facts = build_ai_fact_sheet(profile, compute_metrics_and_dashboard(df, profile))

    result = answer_question("will target increase in the next 10 days", facts, df=df)
    forecast = result["forecast"]
    assert forecast is not None
    assert forecast["kind"] == "holdout_backtest"
    # It must say so rather than inventing a dated future.
    assert "no date column" in result["answer"].lower()
