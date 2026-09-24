"""
The deterministic analytics engine.

Blueprint guardrail (section 9): "Never let the LLM be the source of truth
for arithmetic. The analytics engine calculates values; the LLM receives
those values and explains them." Every number that ever reaches the AI layer
or the dashboard is computed here with pandas — never guessed by a model.
"""
import re
from typing import Any

import numpy as np
import pandas as pd

MAX_ROWS_FOR_MVP = 200_000  # lightweight engine; guard against pathological files


def load_dataframe(path: str) -> pd.DataFrame:
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path, nrows=MAX_ROWS_FOR_MVP)
    else:
        df = pd.read_excel(path, nrows=MAX_ROWS_FOR_MVP)
    # Normalize obvious formatting issues (blueprint section 8: "normalize safe
    # formatting issues such as whitespace and obvious categorical capitalization").
    df.columns = [str(c).strip() for c in df.columns]
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip().replace({"nan": np.nan, "None": np.nan, "": np.nan})
    return df


DATE_HINT = re.compile(r"(date|time|day|month|year|created|updated|timestamp)", re.I)
ID_HINT = re.compile(r"(^id$|_id$|^id_|uuid|identifier|code$)", re.I)


def _infer_column_types(df: pd.DataFrame) -> dict[str, str]:
    types: dict[str, str] = {}
    for col in df.columns:
        series = df[col]
        if ID_HINT.search(col) and series.is_unique:
            types[col] = "identifier"
        elif pd.api.types.is_numeric_dtype(series):
            types[col] = "numeric"
        elif _looks_numeric(series):
            types[col] = "numeric"
        elif DATE_HINT.search(col) or _looks_like_date(series):
            types[col] = "date"
        elif series.nunique(dropna=True) <= max(20, int(0.05 * len(series))):
            types[col] = "categorical"
        else:
            types[col] = "text"
    return types


def _looks_like_date(series: pd.Series, sample: int = 30) -> bool:
    non_null = series.dropna().astype(str).head(sample)
    if non_null.empty:
        return False
    parsed = pd.to_datetime(non_null, errors="coerce", format="mixed")
    return parsed.notna().mean() > 0.8


def _looks_numeric(series: pd.Series, sample: int = 100) -> bool:
    non_null = series.dropna().astype(str).head(sample)
    if non_null.empty:
        return False
    cleaned = non_null.str.replace(r"[^0-9.+\-]", "", regex=True)
    parsed = pd.to_numeric(cleaned, errors="coerce")
    return parsed.notna().mean() > 0.8


def _detect_outliers_iqr(series: pd.Series) -> int:
    clean = series.dropna()
    if len(clean) < 8:
        return 0
    q1, q3 = clean.quantile(0.25), clean.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return 0
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return int(((clean < lower) | (clean > upper)).sum())


def _infer_dataset_category(columns: list[str]) -> str:
    lower_cols = " ".join(c.lower() for c in columns)
    rules = {
        "sales": ["sale", "revenue", "order", "invoice", "price", "quantity"],
        "customers": ["customer", "client", "churn", "signup", "subscriber"],
        "marketing": ["campaign", "click", "impression", "ctr", "conversion", "ad_spend", "spend"],
        "inventory": ["stock", "inventory", "sku", "warehouse", "quantity_on_hand"],
        "hr": ["employee", "salary", "department", "hire_date", "payroll"],
    }
    best_match, best_score = "generic", 0
    for category, keywords in rules.items():
        score = sum(1 for kw in keywords if kw in lower_cols)
        if score > best_score:
            best_match, best_score = category, score
    return best_match


def profile_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    """Blueprint section 8: profile missing values, duplicate rows, unique
    counts, outliers, invalid dates, and suspicious values."""
    col_types = _infer_column_types(df)
    columns_profile = []
    for col in df.columns:
        series = df[col]
        ctype = col_types[col]
        entry: dict[str, Any] = {
            "name": col,
            "type": ctype,
            "missing_count": int(series.isna().sum()),
            "missing_pct": round(float(series.isna().mean() * 100), 2),
            "unique_count": int(series.nunique(dropna=True)),
        }
        if ctype == "numeric":
            clean = pd.to_numeric(series, errors="coerce")
            entry.update({
                "min": _safe_float(clean.min()),
                "max": _safe_float(clean.max()),
                "mean": _safe_float(clean.mean()),
                "outlier_count": _detect_outliers_iqr(clean),
            })
        elif ctype == "date":
            parsed = pd.to_datetime(series, errors="coerce", format="mixed")
            entry.update({
                "invalid_date_count": int(parsed.isna().sum() - series.isna().sum()),
                "min_date": str(parsed.min()) if parsed.notna().any() else None,
                "max_date": str(parsed.max()) if parsed.notna().any() else None,
            })
        elif ctype == "categorical":
            top = series.value_counts(dropna=True).head(5)
            entry["top_values"] = {str(k): int(v) for k, v in top.items()}
        columns_profile.append(entry)

    return {
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "duplicate_row_count": int(df.duplicated().sum()),
        "detected_category": _infer_dataset_category(list(df.columns)),
        "columns": columns_profile,
        "column_types": col_types,
    }


def _safe_float(value) -> float | None:
    if value is None or (isinstance(value, float) and (np.isnan(value) or np.isinf(value))):
        return None
    return round(float(value), 4)


def compute_metrics_and_dashboard(df: pd.DataFrame, profile: dict[str, Any]) -> dict[str, Any]:
    """Blueprint sections 8 & 10: calculate deterministic metrics, recommend
    charts based on field types, and produce a dashboard configuration JSON."""
    col_types = profile["column_types"]
    numeric_cols = [c for c, t in col_types.items() if t == "numeric"]
    date_cols = [c for c, t in col_types.items() if t == "date"]
    categorical_cols = [c for c, t in col_types.items() if t == "categorical"]

    kpi_cards = []
    for col in numeric_cols[:6]:
        clean = pd.to_numeric(df[col], errors="coerce").dropna()
        if clean.empty:
            continue
        kpi_cards.append({
            "metric": col,
            "sum": _safe_float(clean.sum()),
            "average": _safe_float(clean.mean()),
            "min": _safe_float(clean.min()),
            "max": _safe_float(clean.max()),
            "count": int(clean.count()),
        })

    charts = []

    # Time-series chart when a useful date field + numeric field exist.
    if date_cols and numeric_cols:
        date_col, metric_col = date_cols[0], numeric_cols[0]
        ts = df[[date_col, metric_col]].copy()
        ts[date_col] = pd.to_datetime(ts[date_col], errors="coerce", format="mixed")
        ts = ts.dropna(subset=[date_col])
        ts[metric_col] = pd.to_numeric(ts[metric_col], errors="coerce")
        if not ts.empty:
            grouped = ts.set_index(date_col).resample("D")[metric_col].sum().dropna()
            if len(grouped) > 60:  # too granular -> roll up to weekly for a lightweight chart
                grouped = ts.set_index(date_col).resample("W")[metric_col].sum().dropna()
            charts.append({
                "type": "line",
                "title": f"{metric_col} over time",
                "x_field": date_col,
                "y_field": metric_col,
                "data": [{"x": str(idx.date()), "y": _safe_float(val)} for idx, val in grouped.items()],
            })
            # Simple growth rate: first vs last period, deterministic.
            if len(grouped) >= 2 and grouped.iloc[0] not in (0, None):
                growth_pct = _safe_float((grouped.iloc[-1] - grouped.iloc[0]) / grouped.iloc[0] * 100)
            else:
                growth_pct = None
            kpi_cards.append({"metric": f"{metric_col}_growth_pct", "sum": None, "average": None,
                               "min": None, "max": None, "count": None, "growth_pct": growth_pct})

    # Category comparison chart.
    if categorical_cols and numeric_cols:
        cat_col, metric_col = categorical_cols[0], numeric_cols[0]
        grouped = (
            df.groupby(cat_col)[metric_col]
            .apply(lambda s: pd.to_numeric(s, errors="coerce").sum())
            .sort_values(ascending=False)
            .head(10)
        )
        charts.append({
            "type": "bar",
            "title": f"{metric_col} by {cat_col}",
            "x_field": cat_col,
            "y_field": metric_col,
            "data": [{"x": str(k), "y": _safe_float(v)} for k, v in grouped.items()],
        })
        # Top/bottom performers (section 10).
        charts.append({
            "type": "top_bottom",
            "title": f"Top & bottom {cat_col} by {metric_col}",
            "top": [{"label": str(k), "value": _safe_float(v)} for k, v in grouped.head(5).items()],
            "bottom": [{"label": str(k), "value": _safe_float(v)} for k, v in grouped.tail(5).items()],
        })

    # Fallback: if we have a categorical dataset but no numeric field, still
    # show a useful visualization instead of only an empty dashboard.
    if not charts and categorical_cols:
        cat_col = categorical_cols[0]
        grouped = df[cat_col].fillna("(missing)").astype(str).value_counts().head(10)
        charts.append({
            "type": "bar",
            "title": f"Record count by {cat_col}",
            "x_field": cat_col,
            "y_field": "count",
            "data": [{"x": str(k), "y": _safe_float(v)} for k, v in grouped.items()],
        })

    # Correlation between the two strongest numeric fields, if available.
    correlations = []
    if len(numeric_cols) >= 2:
        corr_matrix = df[numeric_cols].apply(pd.to_numeric, errors="coerce").corr(numeric_only=True)
        seen = set()
        for a in numeric_cols:
            for b in numeric_cols:
                if a == b or (b, a) in seen:
                    continue
                seen.add((a, b))
                val = corr_matrix.loc[a, b] if a in corr_matrix and b in corr_matrix else None
                if val is not None and not pd.isna(val):
                    correlations.append({"field_a": a, "field_b": b, "correlation": _safe_float(val)})
        correlations.sort(key=lambda r: abs(r["correlation"] or 0), reverse=True)
        correlations = correlations[:5]

    # Anomaly / data-quality panel (deterministic, from the profile).
    anomalies = []
    for col_entry in profile["columns"]:
        if col_entry.get("outlier_count", 0) > 0:
            anomalies.append({"column": col_entry["name"], "type": "outliers", "count": col_entry["outlier_count"]})
        if col_entry.get("invalid_date_count", 0) > 0:
            anomalies.append({"column": col_entry["name"], "type": "invalid_dates", "count": col_entry["invalid_date_count"]})
        if col_entry["missing_pct"] > 20:
            anomalies.append({"column": col_entry["name"], "type": "high_missing_pct", "value": col_entry["missing_pct"]})
    if profile["duplicate_row_count"] > 0:
        anomalies.append({"column": None, "type": "duplicate_rows", "count": profile["duplicate_row_count"]})

    return {
        "kpi_cards": kpi_cards,
        "charts": charts,
        "correlations": correlations,
        "anomalies": anomalies,
        "data_quality": {
            "row_count": profile["row_count"],
            "duplicate_row_count": profile["duplicate_row_count"],
            "columns_with_missing": [c["name"] for c in profile["columns"] if c["missing_count"] > 0],
        },
    }


def build_ai_fact_sheet(profile: dict[str, Any], dashboard_config: dict[str, Any]) -> dict[str, Any]:
    """The ONLY data the AI layer is allowed to reason over (blueprint section 9:
    'pass only validated aggregates/derived facts to the LLM'). No raw rows,
    no PII-bearing text columns — just the already-computed numbers."""
    return {
        "row_count": profile["row_count"],
        "column_count": profile["column_count"],
        "detected_category": profile["detected_category"],
        "columns": [
            {
                "name": c["name"],
                "type": c["type"],
            }
            for c in profile["columns"]
        ],
        "column_names": [c["name"] for c in profile["columns"]],
        "kpi_cards": dashboard_config["kpi_cards"],
        "top_charts": [
            {"title": c["title"], "type": c["type"]} for c in dashboard_config["charts"]
        ],
        "correlations": dashboard_config["correlations"],
        "anomalies": dashboard_config["anomalies"],
        "data_quality": dashboard_config["data_quality"],
    }
