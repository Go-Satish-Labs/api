"""
Tests for plain-language chart readings.

The failure mode these guard against is a sentence that is confidently wrong:
a reading that contradicts the numbers it claims to describe is worse for a
non-technical reader than no reading at all.
"""
from app.analytics.narration import describe_chart


def test_line_reading_reports_actual_direction_and_change():
    chart = {
        "type": "line", "title": "Units over time",
        "data": [{"x": "2026-01-01", "y": 100}, {"x": "2026-01-02", "y": 150},
                 {"x": "2026-01-03", "y": 200}],
    }
    reading = describe_chart(chart)
    # The title is "Units over time"; the reading must not say
    # "units over time changed over time".
    assert "units over time changed" not in reading["purpose"]
    assert "went up by 100%" in reading["takeaway"]
    assert "100" in reading["takeaway"] and "200" in reading["takeaway"]


def test_line_reading_reports_a_fall_as_a_fall():
    chart = {
        "type": "line", "title": "Units over time",
        "data": [{"x": "a", "y": 200}, {"x": "b", "y": 100}],
    }
    assert "fell by 50%" in describe_chart(chart)["takeaway"]


def test_line_reading_does_not_invent_a_change_when_it_is_flat():
    chart = {
        "type": "line", "title": "Units over time",
        "data": [{"x": "a", "y": 100}, {"x": "b", "y": 102}],
    }
    assert "roughly level" in describe_chart(chart)["takeaway"]


def test_bar_reading_names_the_leader_with_its_share():
    chart = {
        "type": "bar", "title": "Units by Region",
        "data": [{"x": "North", "y": 700}, {"x": "South", "y": 200}, {"x": "East", "y": 100}],
    }
    takeaway = describe_chart(chart)["takeaway"]
    assert "North" in takeaway and "700" in takeaway
    assert "70% of the total" in takeaway
    assert "East" in takeaway


def test_average_chart_does_not_report_a_share_of_total():
    """Per-group means do not sum to a total, so 'x% of the total' is wrong."""
    chart = {
        "type": "bar", "title": "Average Units by Region",
        "data": [{"x": "North", "y": 10}, {"x": "South", "y": 12}],
    }
    takeaway = describe_chart(chart)["takeaway"]
    assert "% of the total" not in takeaway
    assert "higher than" in takeaway


def test_histogram_translates_interval_labels_into_words():
    """pandas renders bins as '(207.267, 219.2]', which means nothing here."""
    chart = {
        "type": "histogram", "title": "Distribution of Units",
        "data": [{"x": "(207.267, 219.2]", "y": 40}, {"x": "(219.2, 231.1]", "y": 10}],
    }
    takeaway = describe_chart(chart)["takeaway"]
    assert "(" not in takeaway
    assert "between 207" in takeaway
    # The busiest of the two bins holds 40 of 50 rows.
    assert "80%" in takeaway


def test_top_bottom_reading_does_not_swap_group_and_metric():
    """The title is 'Top & bottom <GROUP> by <METRIC>' - the reverse order
    of every other chart title."""
    chart = {
        "type": "top_bottom", "title": "Top & bottom Region by Units",
        "top": [{"label": "North", "value": 900}],
        "bottom": [{"label": "East", "value": 100}],
    }
    reading = describe_chart(chart)
    assert reading["purpose"] == "Best and worst region by units."
    assert "9 times as much" in reading["takeaway"]


def test_top_bottom_omits_a_useless_multiple():
    chart = {
        "type": "top_bottom", "title": "Top & bottom Region by Units",
        "top": [{"label": "North", "value": 118}],
        "bottom": [{"label": "East", "value": 100}],
    }
    # "about 1 times as much" says nothing at all.
    assert "times as much" not in describe_chart(chart)["takeaway"]


def test_pie_reading_reports_the_largest_group_as_a_share():
    chart = {
        "type": "pie", "title": "Breakdown by Region",
        "data": [{"x": "North", "y": 75}, {"x": "South", "y": 25}],
    }
    takeaway = describe_chart(chart)["takeaway"]
    assert "North" in takeaway and "75%" in takeaway


def test_every_supported_chart_type_produces_a_purpose():
    charts = [
        {"type": "line", "title": "Units over time", "data": [{"x": "a", "y": 1}, {"x": "b", "y": 2}]},
        {"type": "bar", "title": "Units by Region", "data": [{"x": "N", "y": 1}]},
        {"type": "histogram", "title": "Distribution of Units", "data": [{"x": "(1.0, 2.0]", "y": 3}]},
        {"type": "scatter", "title": "Units vs Price", "x_label": "units", "y_label": "price",
         "data": [{"x": 1, "y": 2}]},
        {"type": "pie", "title": "Breakdown by Region", "data": [{"x": "N", "y": 1}]},
        {"type": "stacked_bar", "title": "Units by Region and Product", "categories": ["N"],
         "series": [{"name": "W", "data": [1]}]},
        {"type": "top_bottom", "title": "Top & bottom Region by Units",
         "top": [{"label": "N", "value": 2}], "bottom": [{"label": "E", "value": 1}]},
    ]
    for chart in charts:
        reading = describe_chart(chart)
        assert reading["purpose"], f"{chart['type']} has no purpose"
        assert reading["takeaway"], f"{chart['type']} has no takeaway"


def test_unknown_chart_type_returns_empty_rather_than_raising():
    reading = describe_chart({"type": "sankey", "title": "Flow"})
    assert reading == {"purpose": "", "takeaway": ""}


def test_empty_or_broken_data_does_not_raise():
    for chart in [
        {"type": "line", "title": "x over time", "data": []},
        {"type": "bar", "title": "Units by Region", "data": [{"x": "N", "y": None}]},
        {"type": "pie", "title": "Breakdown by Region", "data": []},
        {"type": "top_bottom", "title": "Top & bottom Region by Units", "top": [], "bottom": []},
    ]:
        assert "purpose" in describe_chart(chart)
