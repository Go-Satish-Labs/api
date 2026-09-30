"""
Plain-language readings for charts.

A chart labelled "Units by Region" tells a non-technical reader nothing about
what they should take from it. Each function here produces two sentences from
the same numbers the chart draws:

- what the chart *is* (so they know what question it answers), and
- what it *says* (the one finding worth reading).

Both are derived from the chart's own data, so the sentence can never
contradict the picture above it. Wording is deliberately concrete - names and
numbers, not "distribution" or "correlation coefficient".
"""
from __future__ import annotations

import re
from typing import Any, Optional


def _plain(name: Any) -> str:
    """A column name as a person would say it, not as it appears in code."""
    text = str(name or "").strip()
    if not text:
        return "this value"
    text = text.replace("_growth_pct", " growth").replace("_", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def _num(value: Any) -> str:
    if value is None:
        return "unknown"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    magnitude = abs(number)
    if magnitude >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if magnitude >= 10_000:
        return f"{number:,.0f}"
    if magnitude >= 100:
        return f"{number:,.0f}"
    if magnitude >= 1:
        return f"{number:,.1f}"
    if number == 0:
        return "0"
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _values(chart: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in (chart.get("data") or []) if p.get("y") is not None]


def _line(chart: dict[str, Any]) -> dict[str, str]:
    data = _values(chart)
    if len(data) < 2:
        return {"purpose": "Not enough data to draw this chart.", "takeaway": ""}
    first, last = data[0], data[-1]
    # "Units over time" must read as "units", not "units over time".
    label = _plain(
        re.split(r"\s+over time\s*$| by | — ", str(chart.get("title", "")))[0]
    )
    start, end = first.get("y"), last.get("y")
    change = None
    if start not in (None, 0):
        change = (float(end) - float(start)) / abs(float(start)) * 100

    if change is None:
        direction = "stayed at about the same level"
    elif change > 5:
        direction = f"went up by {change:.0f}%"
    elif change < -5:
        direction = f"fell by {abs(change):.0f}%"
    else:
        direction = "stayed roughly level (under 5% change)"

    return {
        "purpose": f"Shows how {label} changed over the period covered by your file.",
        "takeaway": (
            f"{label.capitalize()} {direction}, from {_num(start)} at the start to "
            f"{_num(end)} at the end."
        ),
    }


def _bar(chart: dict[str, Any]) -> dict[str, str]:
    data = _values(chart)
    if not data:
        return {"purpose": "Not enough data to draw this chart.", "takeaway": ""}
    ranked = sorted(data, key=lambda d: float(d["y"]), reverse=True)
    top, bottom = ranked[0], ranked[-1]
    title = str(chart.get("title", ""))
    parts = title.split(" by ", 1)
    metric = _plain(parts[0])
    group = _plain(parts[1]) if len(parts) > 1 else "each group"
    # "share of the total" is meaningless on an average chart - the bars are
    # per-group means, so they do not sum to anything the reader could total.
    is_average = metric.startswith("average") or metric.startswith("avg")

    if is_average and len(ranked) > 1 and float(bottom.get("y", 0)):
        gap = (float(top["y"]) / float(bottom["y"]) - 1) * 100
        comparison = (
            f"that is {gap:.0f}% higher than {str(bottom.get('x')).strip()}"
            if gap >= 5 else f"barely different from {str(bottom.get('x')).strip()}"
        )
        share_phrase = ""
    else:
        total = sum(abs(float(d["y"])) for d in data) or 0
        share = (float(top["y"]) / total * 100) if total else 0
        share_phrase = f" - {share:.0f}% of the total" if share >= 5 and len(ranked) > 1 else ""
        comparison = (
            f"{str(bottom.get('x')).strip()} has the lowest at {_num(bottom.get('y'))}."
            if len(ranked) > 1 else ""
        )

    return {
        "purpose": f"Compares {metric} across {group}, largest first.",
        "takeaway": (
            f"{str(top.get('x')).strip()} has the highest {metric} at "
            f"{_num(top.get('y'))}{share_phrase}"
            f"{'; ' + comparison + '.' if comparison else '.'}"
        ),
    }


def _range_label(raw: Any) -> str:
    """Turn a pandas interval label into words.

    pandas renders bins as "(207.267, 219.2]", which means nothing to a
    reader. "between 207 and 219" is the same information.
    """
    numbers = re.findall(r"-?\d+(?:\.\d+)?", str(raw))
    if len(numbers) >= 2:
        return f"between {_num(float(numbers[0]))} and {_num(float(numbers[1]))}"
    return str(raw)


def _histogram(chart: dict[str, Any]) -> dict[str, str]:
    data = _values(chart)
    if not data:
        return {"purpose": "Not enough data to draw this chart.", "takeaway": ""}
    label = _plain(str(chart.get("title", "")).replace("Distribution of ", ""))
    peak = max(data, key=lambda d: float(d["y"]))
    total = sum(float(d["y"]) for d in data) or 1
    inside = (float(peak["y"]) / total) * 100
    return {
        "purpose": (
            f"Shows how the {label} values are spread out - how many rows fall into each "
            f"range, from smallest to largest."
        ),
        "takeaway": (
            f"The most common range is {_range_label(peak.get('x'))}, holding "
            f"{inside:.0f}% of rows. A tall bar in the middle means typical values cluster "
            f"together; short bars at the edges mean those values are rare."
        ),
    }


def _scatter(chart: dict[str, Any]) -> dict[str, str]:
    a = _plain(chart.get("x_label", "the first value"))
    b = _plain(chart.get("y_label", "the second value"))
    return {
        "purpose": f"Each dot is one row. It shows whether {a} and {b} move together.",
        "takeaway": (
            f"If the dots slope upward, higher {a} goes with higher {b}. If they "
            f"slope downward, one goes up while the other goes down. A shapeless "
            f"blob means {b} is not predictable from {a} alone."
        ),
    }


def _pie(chart: dict[str, Any]) -> dict[str, str]:
    data = _values(chart)
    if not data:
        return {"purpose": "Not enough data to draw this chart.", "takeaway": ""}
    total = sum(float(d["y"]) for d in data) or 1
    ranked = sorted(data, key=lambda d: float(d["y"]), reverse=True)
    top = ranked[0]
    share = float(top["y"]) / total * 100
    label = _plain(str(chart.get("title", "")).replace("Breakdown by ", ""))
    return {
        "purpose": f"Shows what share of your rows belong to each {label}.",
        "takeaway": (
            f"{str(top.get('x')).strip()} is the largest group - {share:.0f}% of all rows"
            + (f", ahead of {str(ranked[1].get('x')).strip()}" if len(ranked) > 1 else "")
            + "."
        ),
    }


def _stacked(chart: dict[str, Any]) -> dict[str, str]:
    title = str(chart.get("title", ""))
    parts = title.split(" by ")
    metric = _plain(parts[0])
    groups = [_plain(p) for p in parts[1].split(" and ")] if len(parts) > 1 else []
    return {
        "purpose": (
            f"Compares {metric} split across {groups[0] if groups else 'the first group'}"
            f"{f' and {groups[1]}' if len(groups) > 1 else ''} at the same time."
        ),
        "takeaway": (
            "Each bar is one value of the first group, and each colour is a part of the "
            "second. A taller bar means more overall; the colours show how that total "
            "splits."
        ),
    }


def _top_bottom(chart: dict[str, Any]) -> dict[str, str]:
    top = chart.get("top") or []
    bottom = chart.get("bottom") or []
    if not top and not bottom:
        return {"purpose": "Not enough data to draw this chart.", "takeaway": ""}
    # The title is "Top & bottom <GROUP> by <METRIC>" - the grouping column
    # comes first here, the opposite of the other chart titles.
    title = str(chart.get("title", ""))
    match = re.match(
        r"top\s*&\s*bottom\s+(?P<group>.+?)\s+by\s+(?P<metric>.+)$", title, re.I
    )
    if match:
        group = _plain(match.group("group"))
        metric = _plain(match.group("metric"))
    else:
        parts = title.split(" by ", 1)
        metric = _plain(parts[0])
        group = _plain(parts[1]) if len(parts) > 1 else "group"

    takeaway = ""
    if top and bottom:
        high, low = top[0], bottom[-1]
        try:
            high_v, low_v = float(high["value"]), float(low["value"])
        except (TypeError, ValueError):
            high_v = low_v = 0.0
        ratio = ""
        if low_v:
            times = high_v / low_v
            # "about 1 times as much" says nothing; only mention the multiple
            # when it is large enough to be the point of the chart.
            if times >= 1.5:
                ratio = f" - about {times:.0f} times as much"
            elif times <= 0.67:
                ratio = ""
        takeaway = (
            f"{str(high.get('label')).strip()} leads with {_num(high.get('value'))}{ratio}; "
            f"{str(low.get('label')).strip()} trails at {_num(low.get('value'))}."
        )
    return {
        "purpose": f"Best and worst {group} by {metric}.",
        "takeaway": takeaway,
    }


_READERS = {
    "line": _line,
    "bar": _bar,
    "histogram": _histogram,
    "scatter": _scatter,
    "pie": _pie,
    "stacked_bar": _stacked,
    "top_bottom": _top_bottom,
}


def describe_chart(chart: dict[str, Any]) -> dict[str, str]:
    """Attach a plain-language reading to a chart spec.

    Returns an empty reading rather than raising on a shape we do not know, so
    a new chart type can be added without breaking the dashboard build.
    """
    kind = chart.get("type")
    reader = _READERS.get(str(kind))
    if not reader:
        return {"purpose": "", "takeaway": ""}
    try:
        result = reader(chart)
    except Exception:  # noqa: BLE001 - a reading is decoration, never fatal
        return {"purpose": "", "takeaway": ""}
    return {
        "purpose": result.get("purpose", ""),
        "takeaway": result.get("takeaway", ""),
    }


def describe_all(charts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**c, "reading": describe_chart(c)} for c in charts]
