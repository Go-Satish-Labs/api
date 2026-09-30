"""
Feedback intake.

Deliberately separate from the analytics tables: this stores what a person
typed, not anything derived from their file. The `context` field is a route
name the frontend supplies, never a filename or file contents, so feedback
does not become a side channel for the data the retention promise covers.
"""
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..deps import get_current_user, get_current_workspace

router = APIRouter(prefix="/feedback", tags=["feedback"])

# Per-user per-day cap. Feedback is free to send, but an unbounded write
# endpoint is a spam target; the cap is generous enough that a user who is
# genuinely reporting several bugs is never blocked.
DAILY_LIMIT = 10


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today(now: datetime | None = None) -> str:
    return (now or _utcnow()).strftime("%Y-%m-%d")


@router.post("", response_model=schemas.FeedbackOut, status_code=201)
def submit_feedback(
    payload: schemas.FeedbackCreate,
    user: models.User = Depends(get_current_user),
    ws: models.Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
):
    message = (payload.message or "").strip()
    if not message:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please write a short message first.")
    if len(message) > 4000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Message is too long (4000 characters max).")

    now = _utcnow()
    since = now - timedelta(days=1)
    recent = db.query(models.Feedback).filter(
        models.Feedback.user_id == user.id,
        models.Feedback.created_at >= since,
    ).count()
    if recent >= DAILY_LIMIT:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You've sent {DAILY_LIMIT} messages in the last 24 hours. We'll get back to you soon.",
        )

    feedback = models.Feedback(
        user_id=user.id,
        workspace_id=ws.id,
        category=(payload.category or "general")[:40],
        rating=payload.rating,
        message=message,
        # Truncated defensively: this is free text from a query param and is
        # stored, so it should never be able to grow without bound.
        context=(payload.context or None) and str(payload.context)[:120],
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return schemas.FeedbackOut(
        id=feedback.id,
        category=feedback.category,
        rating=feedback.rating,
        message=feedback.message,
        status=feedback.status,
        created_at=feedback.created_at,
    )


@router.get("", response_model=list[schemas.FeedbackOut])
def list_my_feedback(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A user's own feedback, so they can see what they have sent."""
    rows = (
        db.query(models.Feedback)
        .filter(models.Feedback.user_id == user.id)
        .order_by(models.Feedback.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        schemas.FeedbackOut(
            id=r.id, category=r.category, rating=r.rating, message=r.message,
            status=r.status, created_at=r.created_at,
        )
        for r in rows
    ]
