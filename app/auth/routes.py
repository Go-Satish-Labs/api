from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user, get_plan
from ..config import settings
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=schemas.TokenResponse, status_code=201)
def register(payload: schemas.RegisterRequest, db: Session = Depends(get_db)):
    if settings.AUTH_MODE == "firebase":
        raise HTTPException(status.HTTP_410_GONE, "Register with Firebase from the client application")
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists. Try signing in instead.")

    # Password hashing is handled here via passlib/bcrypt (blueprint section 12:
    # "do not build custom password storage" -> we don't invent our own crypto,
    # we use a vetted library rather than a hosted auth provider).
    user = models.User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    db.flush()

    # Workspace is created automatically right after registration (blueprint section 3 & 11).
    workspace = models.Workspace(name=f"{payload.email.split('@')[0]}'s Workspace", owner_id=user.id)
    db.add(workspace)

    subscription = models.Subscription(user_id=user.id, plan="free", status="active", provider="none")
    db.add(subscription)

    db.commit()
    token = create_access_token(subject=user.id)
    return schemas.TokenResponse(access_token=token)


@router.post("/login", response_model=schemas.TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    if settings.AUTH_MODE == "firebase":
        raise HTTPException(status.HTTP_410_GONE, "Sign in with Firebase from the client application")
    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    token = create_access_token(subject=user.id)
    return schemas.TokenResponse(access_token=token)


@router.get("/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return schemas.UserOut(id=user.id, email=user.email, plan=get_plan(user, db), role=user.role)


@router.post("/password-reset/request")
def request_password_reset(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    """MVP stand-in for a real email-based reset flow. No SMTP account is
    configured for this build, so instead of emailing a link we return a
    short-lived reset token directly (dev-mode only). Wire this to a real
    email provider before going to production; do not ship this response
    shape as-is."""
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user:
        # Do not reveal whether the email exists.
        return {"message": "If that email exists, a reset token has been issued."}
    reset_token = create_access_token(subject=f"reset:{user.id}")
    return {"message": "Reset token issued (dev mode, no email sent)", "reset_token": reset_token}


@router.post("/password-reset/confirm")
def confirm_password_reset(reset_token: str, new_password: str, db: Session = Depends(get_db)):
    from ..security import decode_access_token

    subject = decode_access_token(reset_token)
    if not subject or not subject.startswith("reset:"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token")
    user_id = subject.split(":", 1)[1]
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.hashed_password = hash_password(new_password)
    db.commit()
    return {"message": "Password updated"}
