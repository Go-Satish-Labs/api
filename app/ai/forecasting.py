"""
Deterministic forecasting for natural-language questions like
"will sales increase in the next 10 days".

Nothing here is generated - every number is a least-squares fit over the
user's own rows, and the method used is reported back alongside the answer so
the UI can say *how* the prediction was reached (blueprint section 9).

Two paths:

- A date column exists  -> fit a linear trend on the resampled series and
  extrapolate it over the requested horizon.
- No date column exists -> a time forecast is meaningless, so instead run a
  holdout backtest (train on the first 80% of rows, predict the last 20%) and
  report the measured accuracy. That keeps the reply honest instead of
  inventing a "future" from data that has no time axis.
"""
from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import pandas as pd

# Minimum signal-to-noise for a quarter-over-quarter move to be called a real
# trend rather than noise. 2.0 is the conventional "probably not a real
# difference" line.
MIN_SIGNAL = 2.0

# Resample codes as a person would say them, for answers shown in the UI.
_FREQUENCY_LABEL = {"D": "day", "W": "week", "MS": "month", "h": "hour"}


def _trend_change_pct(y: np.ndarray) -> float:
    """Percent change from the first quarter's mean to the last quarter's mean.

    Comparing quarter means instead of raw endpoints keeps one odd row from
    flipping the verdict, which a first/last comparison does constantly.
    """
    if len(y) < 4:
        return 0.0
    window = max(1, len(y) // 4)
    first = float(y[:window].mean())
    last = float(y[-window:].mean())
    if first == 0:
        return 0.0
    return (last - first) / abs(first) * 100


def _signal_strength(y: np.ndarray) -> float:
    """How many standard errors separates the last quarter from the first.

    A fixed percentage threshold is not enough: on a noisy series the two
    quarter means drift apart by a few percent through sampling alone, which
    reads as a "decline" that the data does not actually support. Dividing by
    the standard error of the difference asks the right question - is the move
    bigger than the day-to-day noise?
    """
    if len(y) < 4:
        return 0.0
    window = max(1, len(y) // 4)
    first = float(y[:window].mean())
    last = float(y[-window:].mean())
    if len(y) < 3:
        return 0.0
    std = float(np.std(y, ddof=1))
    se_diff = std / math.sqrt(window) * math.sqrt(2)
    if se_diff <= 0:
        return 0.0
    return (last - first) / se_diff


def _direction(change_pct: float, signal: float) -> str:
    if math.isnan(signal) or abs(signal) < MIN_SIGNAL:
        return "flat"
    if abs(change_pct) < 0.01:
        return "increasing" if signal > 0 else "decreasing"
    return "increasing" if change_pct > 0 else "decreasing"


def _resample_freq(series_dates: pd.Series) -> str:
    """Infer a resampling frequency from the median gap between observations."""
    deltas = series_dates.diff().dropna()
    if deltas.empty:
        return "D"
    median = deltas.median()
    days = median.total_seconds() / 86400
    if days < 2:
        return "D"
    if days <= 14:
        return "W"
    return "MS"


def _periods_per_unit(freq: str, count: int, unit: str) -> int:
    """Convert a user horizon into a number of resampled steps.

    The user says a duration ('2 weeks'); the series has a frequency. '2 weeks'
    over daily data is 14 steps, over weekly data is 2 steps, and over monthly
    data is 1 step - so the conversion has to go through days, not divide by
    the unit's length.
    """
    step_days = {"D": 1.0, "W": 7.0, "MS": 30.0}.get(freq, 1.0)
    unit_days = {
        "hour": 1 / 24, "hours": 1 / 24,
        "day": 1.0, "days": 1.0,
        "week": 7.0, "weeks": 7.0,
        "month": 30.0, "months": 30.0,
        "quarter": 90.0, "quarters": 90.0,
        "year": 365.0, "years": 365.0,
    }
    unit_key = (unit or "days").lower()
    days = count * unit_days.get(unit_key, step_days)
    return max(1, int(round(days / step_days)))


def _confidence(r2: float, n_points: int) -> str:
    if r2 >= 0.7 and n_points >= 8:
        return "high"
    if r2 >= 0.4 and n_points >= 5:
        return "moderate"
    return "low"


def forecast_time_series(
    df: pd.DataFrame,
    date_col: str,
    value_col: str,
    periods: int = 10,
    unit: str = "days",
) -> Optional[dict[str, Any]]:
    """Fit a linear trend and extrapolate `periods` ahead. Returns None when
    the inputs can't support a forecast."""
    if date_col not in df.columns or value_col not in df.columns:
        return None

    ts = df[[date_col, value_col]].copy()
    ts[date_col] = pd.to_datetime(ts[date_col], errors="coerce", format="mixed")
    ts = ts.dropna(subset=[date_col])
    ts[value_col] = pd.to_numeric(ts[value_col], errors="coerce")
    ts = ts.dropna(subset=[value_col])
    if len(ts) < 4:
        return None

    freq = _resample_freq(ts[date_col])
    grouped = ts.set_index(date_col)[value_col].resample(freq).sum().dropna()
    if len(grouped) < 3:
        return None

    y = grouped.to_numpy(dtype=float)
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept

    # R^2: how well the straight line explains the history. Reported so the
    # user can judge how much to trust the extrapolation.
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    n_future = _periods_per_unit(freq, periods, unit)
    future_x = np.arange(len(y), len(y) + n_future, dtype=float)
    future_y = slope * future_x + intercept
    # A fitted line can go below zero for a metric that can never be negative
    # (counts, sales). Clamp so the answer stays physically sensible.
    future_y = np.clip(future_y, 0, None)

    offset = pd.tseries.frequencies.to_offset(freq)
    future_dates = pd.Series(
        [grouped.index[-1] + offset * (i + 1) for i in range(n_future)]
    )

    last_actual = float(y[-1])
    predicted = float(future_y[-1])
    trend_change = _trend_change_pct(y)
    signal = _signal_strength(y)
    change_pct = ((predicted - last_actual) / last_actual * 100) if last_actual else None

    return {
        "kind": "time_series",
        "metric": value_col,
        "date_column": date_col,
        "unit": unit,
        "periods_requested": periods,
        "frequency": freq,
        "frequency_label": _FREQUENCY_LABEL.get(freq, freq),
        "last_actual": round(last_actual, 4),
        "last_actual_date": str(grouped.index[-1].date()),
        "predicted_value": round(predicted, 4),
        "predicted_date": str(future_dates.iloc[-1].date()),
        "change_pct": round(change_pct, 1) if change_pct is not None else None,
        "slope_per_period": round(float(slope), 4),
        "trend_change_pct": round(trend_change, 1),
        "signal_strength": round(signal, 2),
        "direction": _direction(trend_change, signal),
        "r2": round(float(r2), 3),
        "confidence": _confidence(r2, len(y)),
        "method": "Linear trend (least squares) over the resampled series",
        "history": [
            {"x": str(idx.date()), "y": round(float(v), 4)}
            for idx, v in grouped.items()
        ],
        "fitted_history": [
            {"x": str(idx.date()), "y": round(float(v), 4)}
            for idx, v in zip(grouped.index, fitted)
        ],
        "forecast": [
            {"x": str(d.date()), "y": round(float(v), 4)}
            for d, v in zip(future_dates, future_y)
        ],
    }


def backtest_no_dates(df: pd.DataFrame, value_col: str, features: list[str]) -> Optional[dict[str, Any]]:
    """No time axis, so instead of a "future" report how well a model does at
    predicting held-out rows. Honest substitute for a forecast."""
    if value_col not in df.columns or not features:
        return None

    y = pd.to_numeric(df[value_col], errors="coerce")
    usable = [f for f in features if f in df.columns]
    if not usable:
        return None
    X = df[usable].apply(pd.to_numeric, errors="coerce")
    frame = X.join(y.rename("__target__")).dropna()
    if len(frame) < 20:
        return None

    Xv = frame[usable].to_numpy(dtype=float)
    yv = frame["__target__"].to_numpy(dtype=float)
    split = int(len(frame) * 0.8)
    if split < 5 or len(frame) - split < 2:
        return None

    try:
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.metrics import r2_score
    except ImportError:
        return None

    model = RandomForestRegressor(n_estimators=60, max_depth=6, random_state=42, n_jobs=-1)
    model.fit(Xv[:split], yv[:split])
    predicted = model.predict(Xv[split:])
    r2 = float(r2_score(yv[split:], predicted))
    mae = float(np.mean(np.abs(yv[split:] - predicted)))

    return {
        "kind": "holdout_backtest",
        "metric": value_col,
        "features_used": usable[:8],
        "rows_total": int(len(frame)),
        "rows_held_out": int(len(frame) - split),
        "r2": round(r2, 3),
        "r2_pct": round(max(r2, 0) * 100, 1),
        "mean_absolute_error": round(mae, 4),
        "direction": "increasing" if (yv[-1] - yv[split - 1]) > 0 else "decreasing",
        "confidence": _confidence(r2, len(frame) - split),
        "method": (
            "80/20 holdout backtest - the model was trained on the first 80% of "
            "rows and scored against the last 20% it never saw"
        ),
        "note": (
            "This dataset has no date column, so there is no time axis to "
            "extrapolate along. Instead of a date-based forecast, this is how "
            "well the model predicts values it was not trained on."
        ),
    }
