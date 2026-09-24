from datetime import datetime

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .database import get_db
from .security import decode_access_token
from .firebase_auth import verify_firebase_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> models.User:
    """Every protected route depends on this. The identity always comes from
    a server-verified JWT, never from a client-supplied user id."""
    if settings.AUTH_MODE == "firebase":
        claims = verify_firebase_token(token)
        uid, email = claims["uid"], claims["email"]
        user = db.query(models.User).filter(models.User.firebase_uid == uid).first()
        if not user:
            user = models.User(email=email, firebase_uid=uid)
            db.add(user)
            db.flush()
            db.add(models.Workspace(name=f"{email.split('@')[0]}'s Workspace", owner_id=user.id))
            db.add(models.Subscription(user_id=user.id, plan="free", status="active", provider="firebase"))
            db.commit()
            db.refresh(user)
        elif user.email != email:
            user.email = email
            db.commit()
    else:
        user_id = decode_access_token(token)
        if not user_id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
        user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


def get_current_workspace(
    user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
) -> models.Workspace:
    """MVP simplification: one workspace per user, auto-created at registration."""
    ws = db.query(models.Workspace).filter(models.Workspace.owner_id == user.id).first()
    if not ws:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    return ws


def get_plan(user: models.User, db: Session) -> str:
    """Server-side source of truth for plan. Never derived from anything the
    client sends (blueprint section 11: 'never trust a client-side premium flag')."""
    sub = db.query(models.Subscription).filter(models.Subscription.user_id == user.id).first()
    if not sub or sub.status != "active":
        return "free"
    if sub.plan == "premium" and sub.current_period_end and sub.current_period_end < datetime.utcnow():
        return "free"  # expired premium silently falls back to free
    return sub.plan


def plan_limits(plan: str) -> dict:
    if plan == "premium":
        return {
            "max_datasets": settings.PREMIUM_MAX_DATASETS,
            "max_storage_mb": settings.PREMIUM_MAX_STORAGE_MB,
            "max_dashboards": settings.PREMIUM_MAX_DASHBOARDS,
            "max_ai_questions": settings.PREMIUM_MAX_AI_QUESTIONS_PER_MONTH,
        }
    return {
        "max_datasets": settings.FREE_MAX_DATASETS,
        "max_storage_mb": settings.FREE_MAX_STORAGE_MB,
        "max_dashboards": settings.FREE_MAX_DASHBOARDS,
        "max_ai_questions": settings.FREE_MAX_AI_QUESTIONS_PER_MONTH,
    }


def require_premium(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)) -> models.User:
    """Dependency for routes that are premium-only (e.g. would gate the mobile
    client's analytics features per blueprint section 7/11)."""
    if get_plan(user, db) != "premium":
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "This feature requires a premium subscription")
    return user
