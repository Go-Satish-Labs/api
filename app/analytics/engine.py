"""
Analytics engine — extended:
- Universal loader: csv, xlsx, xls, txt, json, html, xml
- Labeled / unlabeled detection
- Auto target-variable selection for predictions
- 10+ chart types
- ML: regression, classification (labeled) + clustering (unlabeled)
"""
import re
from typing import Any
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

MAX_ROWS = 200_000


# ── Universal loader ──────────────────────────────────────────────────────────

def load_dataframe(path: str) -> pd.DataFrame:
    suffix = urlsplit(path).path.rsplit(".", 1)[-1].lower()
    try:
        if suffix == "csv":
            df = _try_csv(path)
        elif suffix in ("xlsx", "xls"):
            df = pd.read_excel(path, nrows=MAX_ROWS, engine="openpyxl")
        elif suffix == "json":
            df = _try_json(path)
        elif suffix in ("html", "htm"):
            df = _try_html(path)
        elif suffix == "xml":
            df = _try_xml(path)
        elif suffix == "txt":
            df = _try_txt(path)
        else:
            # last-resort: try csv then json
            try:
                df = _try_csv(path)
            except Exception:
                df = _try_json(path)
    except Exception as exc:
        raise ValueError(f"Could not read file as {suffix}: {exc}") from exc

    df = _normalise(df)
    return df.head(MAX_ROWS)


def _try_csv(path: str) -> pd.DataFrame:
    for sep in [",", ";", "\t", "|"]:
        try:
            df = pd.read_csv(path, sep=sep, nrows=MAX_ROWS, on_bad_lines="skip")
            if len(df.columns) > 1:
                return df
        except Exception:
            continue
    return pd.read_csv(path, nrows=MAX_ROWS, on_bad_lines="skip")


def _try_json(path: str) -> pd.DataFrame:
    for orient in ["records", "split", "index", "columns", "values"]:
        try:
            df = pd.read_json(path, orient=orient)
            if not df.empty:
                return df
        except Exception:
            continue
    raise ValueError("Cannot parse JSON into a table")


def _try_html(path: str) -> pd.DataFrame:
    tables = pd.read_html(path)
    if not tables:
        raise ValueError("No tables found in HTML")
    return max(tables, key=len)


def _try_xml(path: str) -> pd.DataFrame:
    return pd.read_xml(path)


def _try_txt(path: str) -> pd.DataFrame:
    # Try common delimiters; fall back to single-column
    for sep in ["\t", ",", ";", "|", " "]:
        try:
            df = pd.read_csv(path, sep=sep, nrows=MAX_ROWS, on_bad_lines="skip")
            if len(df.columns) > 1:
                return df
        except Exception:
            continue
    return pd.read_csv(path, header=None, names=["value"], nrows=MAX_ROWS)


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip() for c in df.columns]
    # Auto-name unlabeled columns
    unnamed = [c for c in df.columns if re.match(r"^(unnamed|col_?\d+|\d+)$", c, re.I)]
    if len(unnamed) == len(df.columns):
        df.columns = [f"Column_{i+1}" for i in range(len(df.columns))]
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip().replace(
            {"nan": np.nan, "None": np.nan, "": np.nan, "NULL": np.nan, "null": np.nan}
        )
    return df


# ── Column type inference ─────────────────────────────────────────────────────

DATE_HINT = re.compile(r"(date|time|day|month|year|created|updated|timestamp)", re.I)
ID_HINT   = re.compile(r"(^id$|_id$|^id_|uuid|identifier|code$)", re.I)


def _infer_column_types(df: pd.DataFrame) -> dict[str, str]:
    types: dict[str, str] = {}
    for col in df.columns:
        s = df[col]
        if ID_HINT.search(col) and s.nunique() == len(s.dropna()):
            types[col] = "identifier"
        elif pd.api.types.is_numeric_dtype(s):
            types[col] = "numeric"
        elif _looks_numeric(s):
            types[col] = "numeric"
        elif DATE_HINT.search(col) or _looks_like_date(s):
            types[col] = "date"
        elif s.nunique(dropna=True) <= max(20, int(0.05 * len(s))):
            types[col] = "categorical"
        else:
            types[col] = "text"
    return types


def _looks_like_date(s: pd.Series, n: int = 30) -> bool:
    sample = s.dropna().astype(str).head(n)
    if sample.empty:
        return False
    return pd.to_datetime(sample, errors="coerce", format="mixed").notna().mean() > 0.8


def _looks_numeric(s: pd.Series, n: int = 100) -> bool:
    sample = s.dropna().astype(str).head(n)
    if sample.empty:
        return False
    cleaned = sample.str.replace(r"[^0-9.\+\-]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce").notna().mean() > 0.8


def _detect_outliers_iqr(s: pd.Series) -> int:
    clean = s.dropna()
    if len(clean) < 8:
        return 0
    q1, q3 = clean.quantile(0.25), clean.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return 0
    return int(((clean < q1 - 1.5 * iqr) | (clean > q3 + 1.5 * iqr)).sum())


def _infer_dataset_category(columns: list[str]) -> str:
    lc = " ".join(c.lower() for c in columns)
    rules = {
        "sales":     ["sale", "revenue", "order", "invoice", "price", "quantity"],
        "customers": ["customer", "client", "churn", "signup", "subscriber"],
        "marketing": ["campaign", "click", "impression", "ctr", "conversion", "spend"],
        "inventory": ["stock", "inventory", "sku", "warehouse", "quantity_on_hand"],
        "hr":        ["employee", "salary", "department", "hire_date", "payroll"],
        "finance":   ["profit", "loss", "expense", "budget", "cost", "margin"],
        "logistics": ["shipment", "delivery", "freight", "carrier", "tracking"],
    }
    best, score = "generic", 0
    for cat, kws in rules.items():
        s = sum(1 for k in kws if k in lc)
        if s > score:
            best, score = cat, s
    return best


# ── Labeled / unlabeled detection ─────────────────────────────────────────────

def detect_data_structure(df: pd.DataFrame, col_types: dict[str, str]) -> dict[str, Any]:
    """
    Determine whether the dataset is labeled (has a clear target column)
    or unlabeled (no obvious target — suitable for clustering/exploration).
    Returns a human-readable insight dict.
    """
    numeric_cols = [c for c, t in col_types.items() if t == "numeric"]
    cat_cols     = [c for c, t in col_types.items() if t == "categorical"]

    # Heuristic: a column named like a target
    TARGET_HINTS = re.compile(
        r"(target|label|class|category|outcome|result|status|churn|"
        r"survived|diagnosis|fraud|default|response|y$|output)", re.I
    )
    explicit_target = next(
        (c for c in df.columns if TARGET_HINTS.search(c)), None
    )

    # A label column is a *classification* target, and the only strong evidence
    # for one without a naming hint is a genuine two-way split. Treating any
    # low-cardinality column as a label is what previously made "Region" (four
    # regions) the prediction target of a sales file - it is a breakdown, not
    # something to predict, and treating it as one produced a meaningless
    # classifier.
    binary_cats = [c for c in cat_cols if df[c].nunique(dropna=True) == 2]
    weak_label_cats = [c for c in cat_cols if 3 <= df[c].nunique(dropna=True) <= 10]

    is_labeled = bool(explicit_target or binary_cats)
    has_headers = not all(
        re.match(r"^Column_\d+$", c) for c in df.columns
    )

    if explicit_target:
        label_col = explicit_target
        label_type = "classification" if df[label_col].nunique() <= 20 else "regression"
    elif binary_cats:
        label_col = binary_cats[0]
        label_type = "classification"
    elif numeric_cols:
        # No real label: use the most volatile numeric as a regression target
        # but report the data as unlabeled so the UI offers clustering.
        label_col = _pick_target_numeric(df, numeric_cols)
        label_type = "regression"
        is_labeled = False
    else:
        label_col = None
        label_type = None

    return {
        "is_labeled": is_labeled,
        "has_headers": has_headers,
        "label_column": label_col,
        "label_type": label_type,
        "numeric_count": len(numeric_cols),
        "categorical_count": len(cat_cols),
        # Categories available to group or cluster by, so the prediction UI can
        # offer real choices instead of guessing a target.
        "categorical_columns": cat_cols[:8],
        "numeric_columns": numeric_cols[:12],
        "date_column": next((c for c, t in col_types.items() if t == "date"), None),
        "summary": _structure_summary(
            is_labeled, has_headers, label_col, label_type, df,
            weak_label_cats, binary_cats,
        ),
    }


def _pick_target_numeric(df: pd.DataFrame, numeric_cols: list[str]) -> str:
    """Pick the numeric column that varies most (highest std/mean ratio) as proxy target."""
    if len(numeric_cols) == 1:
        return numeric_cols[0]
    scores = {}
    for c in numeric_cols:
        clean = pd.to_numeric(df[c], errors="coerce").dropna()
        if clean.mean() != 0:
            scores[c] = clean.std() / abs(clean.mean())
    return max(scores, key=scores.get) if scores else numeric_cols[-1]


def _structure_summary(is_labeled, has_headers, label_col, label_type, df,
                      weak_label_cats=(), binary_cats=()) -> str:
    parts = []
    if not has_headers:
        parts.append("Your file has no column headers — columns were auto-named.")
    if is_labeled:
        parts.append(
            f"This looks like a labeled dataset. "
            f"The column \"{label_col}\" appears to be the target "
            f"({'categories to predict' if label_type == 'classification' else 'a value to predict'})."
        )
    else:
        parts.append(
            "No clear target column found — treating this as unlabeled data. "
            "We'll group your rows into clusters to find natural patterns."
        )
        if weak_label_cats:
            parts.append(
                f"You can still predict a value you choose (for example "
                f"\"{weak_label_cats[0]}\"), or explore the groupings."
            )
    return " ".join(parts)


# ── Profiling ─────────────────────────────────────────────────────────────────

def profile_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    col_types = _infer_column_types(df)
    columns_profile = []
    for col in df.columns:
        s = df[col]
        ct = col_types[col]
        entry: dict[str, Any] = {
            "name": col, "type": ct,
            "missing_count": int(s.isna().sum()),
            "missing_pct": round(float(s.isna().mean() * 100), 2),
            "unique_count": int(s.nunique(dropna=True)),
        }
        if ct == "numeric":
            clean = pd.to_numeric(s, errors="coerce")
            entry.update({
                "min": _sf(clean.min()), "max": _sf(clean.max()),
                "mean": _sf(clean.mean()), "median": _sf(clean.median()),
                "std": _sf(clean.std()),
                "outlier_count": _detect_outliers_iqr(clean),
            })
        elif ct == "date":
            parsed = pd.to_datetime(s, errors="coerce", format="mixed")
            entry.update({
                "invalid_date_count": int(parsed.isna().sum() - s.isna().sum()),
                "min_date": str(parsed.min()) if parsed.notna().any() else None,
                "max_date": str(parsed.max()) if parsed.notna().any() else None,
            })
        elif ct == "categorical":
            top = s.value_counts(dropna=True).head(5)
            entry["top_values"] = {str(k): int(v) for k, v in top.items()}
        columns_profile.append(entry)

    data_structure = detect_data_structure(df, col_types)

    return {
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "duplicate_row_count": int(df.duplicated().sum()),
        "detected_category": _infer_dataset_category(list(df.columns)),
        "columns": columns_profile,
        "column_types": col_types,
        "data_structure": data_structure,
    }


# ── ML predictions ────────────────────────────────────────────────────────────

def run_predictions(df: pd.DataFrame, profile: dict[str, Any]) -> dict[str, Any]:
    """Run appropriate ML model based on data structure."""
    try:
        from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor, GradientBoostingRegressor
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import LabelEncoder, StandardScaler
        from sklearn.model_selection import cross_val_score
        from sklearn.metrics import r2_score
        import warnings
        warnings.filterwarnings("ignore")
    except ImportError:
        return {"error": "scikit-learn not available"}

    ds = profile.get("data_structure", {})
    col_types = profile["column_types"]
    numeric_cols = [c for c, t in col_types.items() if t == "numeric"]
    cat_cols     = [c for c, t in col_types.items() if t == "categorical"]

    if len(numeric_cols) < 2 and not cat_cols:
        return {"error": "Not enough columns for predictions"}

    # Build feature matrix
    feature_cols = [c for c in numeric_cols if c != ds.get("label_column")][:10]
    if not feature_cols:
        return {"error": "No numeric feature columns available"}

    X_raw = df[feature_cols].apply(pd.to_numeric, errors="coerce").fillna(0)
    scaler = StandardScaler()
    X = scaler.fit_transform(X_raw)

    result: dict[str, Any] = {
        "model_type": None,
        "target_column": ds.get("label_column"),
        "features_used": feature_cols,
        "is_labeled": ds.get("is_labeled", False),
    }

    # ── Labeled: classification or regression ──
    if ds.get("is_labeled") and ds.get("label_column"):
        y_col = ds["label_column"]
        y_raw = df[y_col].dropna()
        X_aligned = X_raw.loc[y_raw.index]
        X_scaled  = scaler.fit_transform(X_aligned)

        if ds.get("label_type") == "classification":
            le = LabelEncoder()
            y = le.fit_transform(y_raw.astype(str))
            clf = RandomForestClassifier(n_estimators=80, max_depth=6, random_state=42, n_jobs=-1)
            clf.fit(X_scaled, y)
            scores = cross_val_score(clf, X_scaled, y, cv=min(5, len(set(y))), scoring="accuracy")
            importances = sorted(
                zip(feature_cols, clf.feature_importances_),
                key=lambda x: x[1], reverse=True
            )
            result.update({
                "model_type": "classification",
                "algorithm": "Random Forest Classifier",
                "accuracy": round(float(scores.mean()), 3),
                "accuracy_pct": round(float(scores.mean()) * 100, 1),
                "classes": list(le.classes_[:10]),
                "feature_importance": [
                    {"feature": f, "importance": round(float(v), 4)}
                    for f, v in importances[:8]
                ],
                "insight": (
                    f"The model predicts \"{y_col}\" with "
                    f"{round(float(scores.mean())*100,1)}% accuracy. "
                    f"The most influential factor is \"{importances[0][0]}\"."
                ),
            })

        else:  # regression
            y = pd.to_numeric(y_raw, errors="coerce").fillna(y_raw.mean())
            reg = GradientBoostingRegressor(n_estimators=100, max_depth=4, random_state=42)
            reg.fit(X_scaled, y)
            preds = reg.predict(X_scaled)
            r2 = r2_score(y, preds)
            importances = sorted(
                zip(feature_cols, reg.feature_importances_),
                key=lambda x: x[1], reverse=True
            )
            result.update({
                "model_type": "regression",
                "algorithm": "Gradient Boosting Regressor",
                "r2_score": round(float(r2), 3),
                "r2_pct": round(float(max(r2, 0)) * 100, 1),
                "feature_importance": [
                    {"feature": f, "importance": round(float(v), 4)}
                    for f, v in importances[:8]
                ],
                "sample_predictions": [
                    {"actual": round(float(a), 2), "predicted": round(float(p), 2)}
                    for a, p in list(zip(y[:8], preds[:8]))
                ],
                "insight": (
                    f"The model explains {round(float(max(r2,0))*100,1)}% of the variation "
                    f"in \"{y_col}\". "
                    f"The strongest predictor is \"{importances[0][0]}\"."
                ),
            })

    # ── Unlabeled: clustering ──
    else:
        n_clusters = min(5, max(2, len(df) // 50))
        km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = km.fit_predict(X)
        cluster_counts = pd.Series(labels).value_counts().sort_index()
        result.update({
            "model_type": "clustering",
            "algorithm": "K-Means Clustering",
            "n_clusters": n_clusters,
            "cluster_sizes": [
                {"cluster": f"Group {i+1}", "count": int(v)}
                for i, v in cluster_counts.items()
            ],
            "insight": (
                f"No target column found, so we grouped your data into "
                f"{n_clusters} natural clusters. "
                f"The largest group has {int(cluster_counts.max())} rows."
            ),
        })

    return result


# ── Dashboard computation ─────────────────────────────────────────────────────

def compute_metrics_and_dashboard(df: pd.DataFrame, profile: dict[str, Any]) -> dict[str, Any]:
    col_types    = profile["column_types"]
    numeric_cols = [c for c, t in col_types.items() if t == "numeric"]
    date_cols    = [c for c, t in col_types.items() if t == "date"]
    cat_cols     = [c for c, t in col_types.items() if t == "categorical"]

    # ── KPI cards ──
    kpi_cards = []
    for col in numeric_cols[:8]:
        clean = pd.to_numeric(df[col], errors="coerce").dropna()
        if clean.empty:
            continue
        kpi_cards.append({
            "metric": col,
            "sum": _sf(clean.sum()),
            "average": _sf(clean.mean()),
            "median": _sf(clean.median()),
            "min": _sf(clean.min()),
            "max": _sf(clean.max()),
            "count": int(clean.count()),
            "std": _sf(clean.std()),
        })

    # Growth KPI from time series
    charts = []
    if date_cols and numeric_cols:
        dc, mc = date_cols[0], numeric_cols[0]
        ts = df[[dc, mc]].copy()
        ts[dc] = pd.to_datetime(ts[dc], errors="coerce", format="mixed")
        ts = ts.dropna(subset=[dc])
        ts[mc] = pd.to_numeric(ts[mc], errors="coerce")
        if not ts.empty:
            freq = "W" if len(ts) > 90 else "D"
            grouped = ts.set_index(dc).resample(freq)[mc].sum().dropna()
            if len(grouped) >= 2:
                charts.append({
                    "type": "line",
                    "title": f"{mc} over time",
                    "data": [{"x": str(idx.date()), "y": _sf(v)} for idx, v in grouped.items()],
                })
                # Rolling 7-period average
                roll = grouped.rolling(7, min_periods=1).mean()
                charts.append({
                    "type": "line",
                    "title": f"{mc} — 7-period rolling average",
                    "data": [{"x": str(idx.date()), "y": _sf(v)} for idx, v in roll.items()],
                })
                growth = _sf((grouped.iloc[-1] - grouped.iloc[0]) / grouped.iloc[0] * 100) if grouped.iloc[0] else None
                kpi_cards.append({
                    "metric": f"{mc}_growth_pct",
                    "sum": None, "average": None, "min": None, "max": None, "count": None,
                    "growth_pct": growth,
                })

    # Bar: category vs numeric
    if cat_cols and numeric_cols:
        cc, mc = cat_cols[0], numeric_cols[0]
        grp = (
            df.groupby(cc)[mc]
            .apply(lambda s: pd.to_numeric(s, errors="coerce").sum())
            .sort_values(ascending=False)
        )
        charts.append({
            "type": "bar",
            "title": f"{mc} by {cc}",
            "data": [{"x": str(k), "y": _sf(v)} for k, v in grp.head(12).items()],
        })
        # Top/bottom
        charts.append({
            "type": "top_bottom",
            "title": f"Top & bottom {cc} by {mc}",
            "top":    [{"label": str(k), "value": _sf(v)} for k, v in grp.head(5).items()],
            "bottom": [{"label": str(k), "value": _sf(v)} for k, v in grp.tail(5).items()],
        })
        # Average instead of sum
        grp_avg = (
            df.groupby(cc)[mc]
            .apply(lambda s: pd.to_numeric(s, errors="coerce").mean())
            .sort_values(ascending=False)
        )
        charts.append({
            "type": "bar",
            "title": f"Average {mc} by {cc}",
            "data": [{"x": str(k), "y": _sf(v)} for k, v in grp_avg.head(12).items()],
        })

    # Histogram for each numeric col (up to 4)
    for col in numeric_cols[:4]:
        clean = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(clean) < 5:
            continue
        bins = pd.cut(clean, bins=min(15, len(clean) // 5 + 1))
        hist = bins.value_counts().sort_index()
        charts.append({
            "type": "histogram",
            "title": f"Distribution of {col}",
            "data": [{"x": str(interval), "y": int(cnt)} for interval, cnt in hist.items()],
        })

    # Scatter: top 2 correlated numeric cols
    if len(numeric_cols) >= 2:
        a, b = numeric_cols[0], numeric_cols[1]
        sample = df[[a, b]].apply(pd.to_numeric, errors="coerce").dropna().head(300)
        if len(sample) >= 5:
            charts.append({
                "type": "scatter",
                "title": f"{a} vs {b}",
                "x_label": a, "y_label": b,
                "data": [{"x": _sf(r[a]), "y": _sf(r[b])} for _, r in sample.iterrows()],
            })

    # Category distribution (pie-style data)
    for col in cat_cols[:2]:
        vc = df[col].value_counts(dropna=True).head(8)
        charts.append({
            "type": "pie",
            "title": f"Breakdown by {col}",
            "data": [{"x": str(k), "y": int(v)} for k, v in vc.items()],
        })

    # Stacked bar: 2 categoricals
    if len(cat_cols) >= 2 and numeric_cols:
        c1, c2, mc = cat_cols[0], cat_cols[1], numeric_cols[0]
        pivot = (
            df.groupby([c1, c2])[mc]
            .apply(lambda s: pd.to_numeric(s, errors="coerce").sum())
            .unstack(fill_value=0)
        )
        if pivot.shape[0] <= 12 and pivot.shape[1] <= 8:
            charts.append({
                "type": "stacked_bar",
                "title": f"{mc} by {c1} and {c2}",
                "categories": list(pivot.index.astype(str)),
                "series": [
                    {"name": str(col), "data": [_sf(v) for v in pivot[col]]}
                    for col in pivot.columns
                ],
            })

    # Fallback
    if not charts and cat_cols:
        col = cat_cols[0]
        vc = df[col].fillna("(missing)").astype(str).value_counts().head(12)
        charts.append({
            "type": "bar",
            "title": f"Record count by {col}",
            "data": [{"x": str(k), "y": _sf(v)} for k, v in vc.items()],
        })

    # Correlations
    correlations = []
    if len(numeric_cols) >= 2:
        corr = df[numeric_cols].apply(pd.to_numeric, errors="coerce").corr(numeric_only=True)
        seen: set = set()
        for a in numeric_cols:
            for b in numeric_cols:
                if a == b or (b, a) in seen:
                    continue
                seen.add((a, b))
                val = corr.loc[a, b] if a in corr and b in corr else None
                if val is not None and not pd.isna(val):
                    correlations.append({"field_a": a, "field_b": b, "correlation": _sf(val)})
        correlations.sort(key=lambda r: abs(r["correlation"] or 0), reverse=True)
        correlations = correlations[:8]

    # Anomalies
    anomalies = []
    for ce in profile["columns"]:
        if ce.get("outlier_count", 0) > 0:
            anomalies.append({"column": ce["name"], "type": "outliers", "count": ce["outlier_count"]})
        if ce.get("invalid_date_count", 0) > 0:
            anomalies.append({"column": ce["name"], "type": "invalid_dates", "count": ce["invalid_date_count"]})
        if ce["missing_pct"] > 20:
            anomalies.append({"column": ce["name"], "type": "high_missing_pct", "value": ce["missing_pct"]})
    if profile["duplicate_row_count"] > 0:
        anomalies.append({"column": None, "type": "duplicate_rows", "count": profile["duplicate_row_count"]})

    # Predictions
    predictions = run_predictions(df, profile)

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
        "data_structure": profile.get("data_structure", {}),
        "predictions": predictions,
    }


def build_ai_fact_sheet(profile: dict[str, Any], dashboard_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_count": profile["row_count"],
        "column_count": profile["column_count"],
        "detected_category": profile["detected_category"],
        "data_structure": profile.get("data_structure", {}),
        "columns": [{"name": c["name"], "type": c["type"]} for c in profile["columns"]],
        "column_names": [c["name"] for c in profile["columns"]],
        "kpi_cards": dashboard_config["kpi_cards"],
        "top_charts": [{"title": c["title"], "type": c["type"]} for c in dashboard_config["charts"]],
        "correlations": dashboard_config["correlations"],
        "anomalies": dashboard_config["anomalies"],
        "data_quality": dashboard_config["data_quality"],
        "predictions": dashboard_config.get("predictions", {}),
    }


def _sf(v) -> float | None:
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return None
    return round(float(v), 4)
