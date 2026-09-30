"""
Data retention: actually delete uploaded data, and tell the truth about it.

The retention window in settings.DATA_RETENTION_HOURS is the single source of
truth for both the purge and the copy shown in the UI. That is deliberate: a
privacy notice is a claim about behaviour, and a notice whose number is
maintained separately from the number the code enforces is a claim that drifts
out of date silently. Reading the window from one place makes it impossible
for the message to disagree with what the server does.

Note on scope: deleting a Dataset row cascades to its dashboards, and deleting
a Workspace would cascade to the datasets. This purges per-dataset on age, not
per-account, so a user's dashboard links that point at a shared report survive
- shared reports hold aggregates, never source rows.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from . import models
from .config import settings
from .storage.local_storage import delete_file


def retention_window() -> timedelta:
    return timedelta(hours=settings.DATA_RETENTION_HOURS)


def _log(message: str) -> None:
    print(f"[retention] {message}", file=sys.stderr)


def purge_expired_datasets(db: Session, now: datetime | None = None) -> int:
    """Delete uploaded files and rows older than the retention window.

    Returns how many datasets were removed. Safe to call repeatedly: it only
    ever touches rows already past the deadline.
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - retention_window()
    expired = (
        db.query(models.Dataset)
        .filter(models.Dataset.created_at < cutoff)
        .all()
    )
    if not expired:
        return 0

    for dataset in expired:
        # The stored file is the part that actually holds the user's data, so
        # it goes first. A failure to remove it must not strand the row.
        try:
            if dataset.stored_path:
                delete_file(dataset.stored_path)
        except Exception as exc:  # noqa: BLE001
            _log(f"could not delete file for dataset {dataset.id}: {exc}")
        db.delete(dataset)

    db.commit()
    _log(f"purged {len(expired)} dataset(s) uploaded before {cutoff:%Y-%m-%d %H:%M} UTC")
    return len(expired)


def hours_remaining(created_at: datetime | None, now: datetime | None = None) -> float | None:
    """Hours left before a dataset is purged. None when unknown."""
    if not created_at:
        return None
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    remaining = (created_at + retention_window()) - now
    return max(0.0, remaining.total_seconds() / 3600)


def retention_notice() -> dict[str, Any]:
    """The single source of truth for the privacy message the UI displays.

    Generated from settings, so the copy can never claim a different window
    than the purge actually enforces.
    """
    hours = settings.DATA_RETENTION_HOURS
    # Stated in hours, matching the setting exactly. Converting 24 to "1 day"
    # here previously produced "1 hours", and any rounding is a chance for the
    # copy to stop matching the enforced window.
    window = f"{hours} hours"
    return {
        "retention_hours": hours,
        "window": window,
        "headline": "Your data is deleted automatically",
        "message": (
            f"For your privacy we delete every uploaded file {window} after upload. "
            f"We keep only the numbers we calculated from it, never the file itself."
        ),
        "short": f"Files are deleted {window} after upload.",
    }
