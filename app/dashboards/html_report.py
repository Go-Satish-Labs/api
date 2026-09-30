"""
Self-contained HTML report rendering.

A shared dashboard has to be a single file that opens correctly with no
network, no JavaScript, and no CDN - a link pasted into a chat or an email
should just work. So the charts are drawn as inline SVG rather than embedded
from a charting library.

Privacy: this renderer only ever receives the *computed* dashboard config
(aggregates, chart specs, quality counts). It is never handed the source rows,
so a shared link cannot leak the uploaded data itself. That matters here
because the product promises uploaded files are not retained.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Optional

# ── Formatting helpers ───────────────────────────────────────────────────────

def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        number = float(value)
        magnitude = abs(number)
        if magnitude >= 1_000_000:
            return f"{number / 1_000_000:.2f}M"
        if magnitude >= 10_000:
            return f"{number:,.0f}"
        if magnitude >= 100:
            return f"{number:,.1f}"
        if magnitude >= 1:
            return f"{number:,.2f}"
        return f"{number:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _label(raw: Any) -> str:
    text = str(raw or "")
    if text.lower().endswith("_growth_pct"):
        text = text[: -len("_growth_pct")] + " Growth"
    return text.replace("_", " ").replace("  ", " ").strip().title()


# ── Inline SVG charts ─────────────────────────────────────────────────────────

_W, _H = 620, 240
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 56, 12, 14, 42


def _frame(values: list[float]) -> tuple[float, float]:
    finite = [v for v in values if v is not None and v == v]
    if not finite:
        return 0.0, 1.0
    low, high = min(finite), max(finite)
    if high == low:
        # A flat series: pad so the line renders mid-height instead of on an edge.
        return low - abs(low or 1) * 0.1, high + abs(high or 1) * 0.1
    margin = (high - low) * 0.08
    return low - margin, high + margin


def _axes(low: float, high: float) -> str:
    parts = []
    for i in range(5):
        value = low + (high - low) * i / 4
        y = _PAD_T + (_H - _PAD_T - _PAD_B) * (1 - i / 4)
        parts.append(
            f'<line x1="{_PAD_L}" y1="{y:.1f}" x2="{_W - _PAD_R}" y2="{y:.1f}" '
            f'stroke="#e8e8e8" stroke-width="1"/>'
            f'<text x="{_PAD_L - 6}" y="{y + 3:.1f}" text-anchor="end" '
            f'font-size="10" fill="#9aa0a6">{_esc(_fmt(value))}</text>'
        )
    return "".join(parts)


def _svg_bar(data: list[dict[str, Any]], title: str) -> str:
    if not data:
        return ""
    low, high = _frame([d.get("y") or 0 for d in data])
    span = (high - low) or 1
    plot_w = _W - _PAD_L - _PAD_R
    plot_h = _H - _PAD_T - _PAD_B
    slot = plot_w / len(data)
    bar_w = min(slot * 0.62, 56)
    zero_y = _PAD_T + plot_h * (1 - (0 - low) / span)
    zero_y = max(_PAD_T, min(_H - _PAD_B, zero_y))

    bars, labels = [], []
    for i, point in enumerate(data[:12]):
        value = point.get("y") or 0
        y_value = _PAD_T + plot_h * (1 - (value - low) / span)
        top = min(y_value, zero_y)
        height = max(1.5, abs(zero_y - y_value))
        x = _PAD_L + slot * i + (slot - bar_w) / 2
        bars.append(
            f'<rect x="{x:.1f}" y="{top:.1f}" width="{bar_w:.1f}" height="{height:.1f}" '
            f'rx="3" fill="#0a0a0a" fill-opacity="{1 if i < 3 else 0.45}"><title>'
            f'{_esc(point.get("x"))}: {_esc(_fmt(value))}</title></rect>'
        )
        labels.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{_H - _PAD_B + 14}" text-anchor="middle" '
            f'font-size="10" fill="#6b6b6b">{_esc(str(point.get("x"))[:10])}</text>'
        )
    return (
        f'<figure><figcaption>{_esc(title)}</figcaption>'
        f'<svg viewBox="0 0 {_W} {_H}" width="100%" role="img" '
        f'aria-label="{_esc(title)}">{_axes(low, high)}{"".join(bars)}{"".join(labels)}</svg></figure>'
    )


def _svg_line(data: list[dict[str, Any]], title: str, dashed_from: Optional[int] = None) -> str:
    if len(data) < 2:
        return ""
    low, high = _frame([d.get("y") or 0 for d in data])
    span = (high - low) or 1
    plot_w = _W - _PAD_L - _PAD_R
    plot_h = _H - _PAD_T - _PAD_B
    step = plot_w / (len(data) - 1)

    def point(index: int) -> tuple[float, float]:
        value = data[index].get("y") or 0
        return (
            _PAD_L + step * index,
            _PAD_T + plot_h * (1 - (value - low) / span),
        )

    def path(start: int, end: int) -> str:
        return " ".join(
            ("M" if i == start else "L") + f"{point(i)[0]:.1f},{point(i)[1]:.1f}"
            for i in range(start, min(end + 1, len(data)))
        )

    series = ""
    if dashed_from is not None and 0 < dashed_from < len(data):
        series += f'<path d="{path(0, dashed_from)}" fill="none" stroke="#0a0a0a" stroke-width="2"/>'
        series += f'<path d="{path(dashed_from - 1, len(data) - 1)}" fill="none" stroke="#6b6b6b" stroke-width="2" stroke-dasharray="6 4"/>'
    else:
        series += f'<path d="{path(0, len(data) - 1)}" fill="none" stroke="#0a0a0a" stroke-width="2"/>'

    ticks = []
    for i in range(min(6, len(data))):
        index = int(i * (len(data) - 1) / max(1, min(6, len(data)) - 1)) if min(6, len(data)) > 1 else 0
        x, _ = point(index)
        ticks.append(
            f'<text x="{x:.1f}" y="{_H - _PAD_B + 14}" text-anchor="middle" '
            f'font-size="10" fill="#6b6b6b">{_esc(str(data[index].get("x"))[:10])}</text>'
        )
    return (
        f'<figure><figcaption>{_esc(title)}</figcaption>'
        f'<svg viewBox="0 0 {_W} {_H}" width="100%" role="img" '
        f'aria-label="{_esc(title)}">{_axes(low, high)}{series}{"".join(ticks)}</svg></figure>'
    )


def _render_chart(chart: dict[str, Any]) -> str:
    kind = chart.get("type")
    title = _label(chart.get("title", ""))
    if kind in ("bar", "histogram"):
        return _svg_bar(chart.get("data") or [], title)
    if kind == "line":
        return _svg_line(chart.get("data") or [], title)
    if kind == "top_bottom":
        top = chart.get("top") or []
        bottom = chart.get("bottom") or []
        rows = "".join(
            f"<tr><td>{_esc(t.get('label'))}</td><td class='num'>{_esc(_fmt(t.get('value')))}</td></tr>"
            for t in top[:5]
        ) + "".join(
            f"<tr><td>{_esc(t.get('label'))}</td><td class='num'>{_esc(_fmt(t.get('value')))}</td></tr>"
            for t in bottom[:5]
        )
        if not rows:
            return ""
        return (
            f"<figure><figcaption>{_esc(title)}</figcaption>"
            f"<table><thead><tr><th>Group</th><th class='num'>Value</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></figure>"
        )
    if kind == "pie":
        data = (chart.get("data") or [])[:8]
        total = sum((d.get("y") or 0) for d in data) or 1
        rows = "".join(
            f"<tr><td>{_esc(d.get('x'))}</td>"
            f"<td class='num'>{_esc(_fmt(d.get('y')))}</td>"
            f"<td class='num'>{((d.get('y') or 0) / total) * 100:.0f}%</td></tr>"
            for d in data
        )
        if not rows:
            return ""
        return (
            f"<figure><figcaption>{_esc(title)}</figcaption>"
            f"<table><thead><tr><th>Group</th><th class='num'>Value</th><th class='num'>Share</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></figure>"
        )
    if kind in ("scatter", "stacked_bar"):
        # These need a real axis to be readable; a table is honest where an
        # approximate drawing would not be.
        data = (chart.get("data") or [])[:12]
        rows = "".join(
            f"<tr><td>{_esc(d.get('x'))}</td><td class='num'>{_esc(_fmt(d.get('y')))}</td></tr>"
            for d in data
        )
        if not rows:
            return ""
        return (
            f"<figure><figcaption>{_esc(title)}</figcaption>"
            f"<table><thead><tr><th>{_esc(_label(chart.get('x_label') or 'Value'))}</th>"
            f"<th class='num'>{_esc(_label(chart.get('y_label') or 'Value'))}</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></figure>"
        )
    return ""


# ── Report sections ───────────────────────────────────────────────────────────

def _kpi_section(config: dict[str, Any]) -> str:
    cards = []
    for kpi in config.get("kpi_cards", [])[:10]:
        growth = kpi.get("growth_pct")
        if growth is not None:
            value = f"{'+' if growth >= 0 else ''}{growth}%"
            note = "change over the period"
        else:
            value = _fmt(kpi.get("sum"))
            note = f"avg {_fmt(kpi.get('average'))}"
        cards.append(
            f"<div class='kpi'><div class='kpi-label'>{_esc(_label(kpi.get('metric')))}</div>"
            f"<div class='kpi-value'>{_esc(value)}</div>"
            f"<div class='kpi-note'>{_esc(note)}</div></div>"
        )
    if not cards:
        return ""
    return f"<h2>Key numbers</h2><div class='kpis'>{''.join(cards)}</div>"


def _prediction_section(prediction: Optional[dict[str, Any]]) -> str:
    if not prediction or prediction.get("error"):
        return ""
    kind = prediction.get("model_type")
    heading = {
        "clustering": "Groups found in the data",
        "classification": "Category prediction",
        "regression": "Number prediction",
    }.get(kind, "Prediction")

    parts = [f"<h2>{_esc(heading)}</h2>"]
    if prediction.get("target_column"):
        parts.append(
            f"<p class='muted'>Predicting <strong>{_esc(_label(prediction['target_column']))}</strong> "
            f"from {len(prediction.get('features_used') or [])} other column(s).</p>"
        )
    if prediction.get("insight"):
        parts.append(f"<p class='callout'>{_esc(prediction['insight'])}</p>")

    if prediction.get("accuracy_pct") is not None:
        parts.append(
            f"<p><strong>Accuracy {_esc(prediction['accuracy_pct'])}%</strong> "
            f"<span class='muted'>(measured on rows held back from training)</span></p>"
        )
    if prediction.get("r2_pct") is not None:
        parts.append(
            f"<p><strong>Explains {_esc(prediction['r2_pct'])}% of the variation</strong> "
            f"<span class='muted'>(measured on rows held back from training)</span></p>"
        )

    importance = prediction.get("feature_importance") or []
    if importance:
        rows = "".join(
            f"<tr><td>{_esc(_label(f['feature']))}</td>"
            f"<td class='num'>{f['importance'] * 100:.1f}%</td></tr>"
            for f in importance
        )
        parts.append(
            "<h3>What drives it</h3><table><thead><tr><th>Column</th>"
            f"<th class='num'>Influence</th></tr></thead><tbody>{rows}</tbody></table>"
        )

    profiles = {p["cluster"]: p.get("notable", []) for p in (prediction.get("cluster_profiles") or [])}
    clusters = prediction.get("cluster_sizes") or []
    if clusters:
        rows = ""
        for cluster in clusters:
            notes = profiles.get(cluster["cluster"]) or []
            description = ", ".join(notes) or "&mdash;"
            rows += (
                f"<tr><td><strong>{_esc(cluster['cluster'])}</strong></td>"
                f"<td>{_esc(description)}</td>"
                f"<td class='num'>{cluster['count']:,}</td></tr>"
            )
        parts.append(
            "<h3>Groups</h3><table><thead><tr><th>Group</th><th>What stands out</th>"
            f"<th class='num'>Rows</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    return "".join(parts)


def _quality_section(config: dict[str, Any]) -> str:
    quality = config.get("data_quality", {})
    anomalies = config.get("anomalies", [])
    if not anomalies:
        return "<h2>Data health</h2><p>No data-quality issues were detected.</p>"
    rows = "".join(
        f"<tr><td>{_esc(_label(a.get('type')))}</td>"
        f"<td>{_esc(_label(a.get('column')) if a.get('column') else 'the file')}</td>"
        f"<td class='num'>{_esc(a.get('count', a.get('value', '—')))}</td></tr>"
        for a in anomalies[:12]
    )
    return (
        f"<h2>Data health</h2><p class='muted'>{quality.get('row_count', 0):,} rows, "
        f"{len(quality.get('columns_with_missing', []))} column(s) with gaps.</p>"
        f"<table><thead><tr><th>Issue</th><th>Where</th><th class='num'>How many</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


CSS = """
*{box-sizing:border-box}
body{margin:0;padding:40px 24px;background:#f6f7f9;color:#0a0a0a;
  font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:920px;margin:0 auto;background:#fff;border:1px solid #e8eaed;
  border-radius:16px;padding:36px 40px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-0.02em}
h2{font-size:18px;margin:34px 0 12px;padding-top:18px;border-top:1px solid #eef0f2}
h3{font-size:14px;margin:22px 0 8px;color:#6b6b6b;text-transform:uppercase;letter-spacing:.06em}
.muted{color:#6b6b6b;font-size:13px}
.badge{display:inline-block;background:#eef0f2;border-radius:999px;padding:4px 12px;
  font-size:12px;font-weight:600;color:#3c4043;margin-bottom:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.kpi{border:1px solid #e8eaed;border-radius:12px;padding:12px 14px}
.kpi-label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#6b6b6b}
.kpi-value{font-size:22px;font-weight:800;margin:4px 0 2px}
.kpi-note{font-size:11px;color:#9aa0a6}
figure{margin:0 0 22px;border:1px solid #e8eaed;border-radius:12px;padding:14px 16px}
figcaption{font-size:12px;font-weight:700;color:#3c4043;margin-bottom:8px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #f1f3f4}
th{color:#6b6b6b;font-weight:600;font-size:12px}
.num{text-align:right;font-variant-numeric:tabular-nums}
.callout{background:#f6f7f9;border:1px solid #e8eaed;border-radius:10px;padding:12px 14px;font-size:14px}
footer{margin-top:36px;padding-top:16px;border-top:1px solid #eef0f2;font-size:12px;color:#9aa0a6}
@media print{body{background:#fff;padding:0}.wrap{border:none;padding:0}}
"""


def render_dashboard_html(
    config: dict[str, Any],
    title: str,
    mode: str = "history",
    prediction: Optional[dict[str, Any]] = None,
    dataset_filename: str = "",
) -> str:
    """Render a complete, dependency-free HTML dashboard.

    `mode` is history | prediction. In prediction mode the KPI and chart
    sections are replaced by the prediction result, because mixing "what
    happened" with "what the model expects" in one report reads as though the
    projection were measured.
    """
    generated = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    mode_label = "Prediction" if mode == "prediction" else "History"

    parts = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{_esc(title)}</title><style>{CSS}</style></head><body><div class='wrap'>",
        f"<span class='badge'>{_esc(mode_label)} dashboard</span>",
        f"<h1>{_esc(title)}</h1>",
        f"<p class='muted'>{_esc(dataset_filename)} &middot; generated {generated}</p>",
    ]

    if mode == "prediction":
        prediction = prediction or config.get("predictions")
        section = _prediction_section(prediction)
        parts.append(section or "<h2>Prediction</h2><p>No prediction result was supplied.</p>")
    else:
        parts.append(_kpi_section(config))
        charts = "".join(_render_chart(c) for c in (config.get("charts") or []))
        if charts:
            parts.append(f"<h2>Charts</h2>{charts}")
        else:
            parts.append("<h2>Charts</h2><p class='muted'>No charts could be drawn for this file.</p>")
        parts.append(_quality_section(config))

    parts.append(
        "<footer>Every number here was calculated directly from the uploaded file. "
        "This report contains aggregates only - no rows from the source data are included. "
        "Generated by Analytrix.</footer>"
    )
    parts.append("</div></body></html>")
    return "".join(parts)
