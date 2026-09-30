"""
Tests for the HTML report renderer and the share-link flow.

The share link is public by design, so the two things worth asserting are
that it is scoped (unknown/expired/revoked all 404 or 410, never render) and
that the rendered document cannot carry the uploaded rows.
"""
import pytest

from app.dashboards.html_report import render_dashboard_html

CONFIG = {
    "kpi_cards": [
        {"metric": "Units", "sum": 5000, "average": 80, "growth_pct": None},
        {"metric": "Units_growth_pct", "sum": None, "average": None, "growth_pct": 12.5},
    ],
    "charts": [
        {"type": "line", "title": "Units over time",
         "data": [{"x": "2026-01-01", "y": 50}, {"x": "2026-01-02", "y": 51},
                  {"x": "2026-01-03", "y": 52}]},
        {"type": "bar", "title": "Units by Region",
         "data": [{"x": "North", "y": 300}, {"x": "South", "y": 200}]},
        {"type": "top_bottom", "title": "Top and bottom Region",
         "top": [{"label": "North", "value": 300}], "bottom": [{"label": "South", "value": 200}]},
    ],
    "correlations": [],
    "anomalies": [{"column": "Units", "type": "outliers", "count": 3}],
    "data_quality": {"row_count": 60, "duplicate_row_count": 0, "columns_with_missing": []},
    "predictions": {},
}


def test_report_is_self_contained():
    html = render_dashboard_html(CONFIG, "My dashboard", "history", dataset_filename="s.csv")
    assert html.startswith("<!DOCTYPE html>")
    # No network, no scripts: a pasted link must render offline and cannot run
    # anything on the reader's machine.
    assert "<script" not in html.lower()
    assert "http://" not in html
    assert "https://" not in html
    assert "cdn" not in html.lower()


def test_report_renders_charts_as_inline_svg():
    html = render_dashboard_html(CONFIG, "My dashboard", "history")
    assert html.count("<svg") >= 2
    assert "<figure>" in html


def test_report_labels_growth_card_readably():
    html = render_dashboard_html(CONFIG, "My dashboard", "history")
    # "Units_growth_pct" must not reach the reader as a raw column name.
    assert "Units_growth_pct" not in html
    assert "Units Growth" in html
    assert "+12.5%" in html


def test_prediction_mode_shows_the_model_not_the_history():
    prediction = {
        "model_type": "clustering",
        "algorithm": "K-Means Clustering",
        "features_used": ["Ad_Spend", "Visits"],
        "n_clusters": 2,
        "cluster_sizes": [{"cluster": "Group 1", "count": 30}, {"cluster": "Group 2", "count": 30}],
        "cluster_profiles": [{"cluster": "Group 1", "notable": ["highest Ad_Spend"]}],
        "insight": "Grouped 60 rows into 2 groups.",
    }
    html = render_dashboard_html(CONFIG, "P", "prediction", prediction=prediction)
    assert "Groups found in the data" in html
    assert "highest Ad_Spend" in html
    # Mixing measured history with a projection reads as though the forecast
    # were observed, so history is left out of a prediction report.
    assert "Key numbers" not in html


def test_report_escapes_html_in_labels():
    hostile = {
        "kpi_cards": [{"metric": "<script>alert(1)</script>", "sum": 1}],
        "charts": [], "anomalies": [],
        "data_quality": {"row_count": 1, "duplicate_row_count": 0, "columns_with_missing": []},
    }
    html = render_dashboard_html(hostile, "<img onerror=x>", "history")
    # Column labels are title-cased before escaping, so compare case
    # insensitively: the invariant is that no raw tag survives, not the exact
    # capitalisation of the escaped text.
    assert "<script" not in html.lower()
    assert "<img" not in html.lower()
    assert "&lt;" in html


def test_report_states_that_it_holds_no_rows():
    html = render_dashboard_html(CONFIG, "My dashboard", "history")
    assert "no rows from the source data" in html


def test_empty_config_still_renders():
    empty = {"kpi_cards": [], "charts": [], "anomalies": [],
             "data_quality": {"row_count": 0, "duplicate_row_count": 0, "columns_with_missing": []}}
    html = render_dashboard_html(empty, "Empty", "history")
    assert html.startswith("<!DOCTYPE html>")
    assert "Empty" in html
