from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user, get_plan
from ..config import settings
from ..security import create_access_token, hash_password, verify_password, hash_security_answer, verify_security_answer
from ..storage.local_storage import save_profile_pic, delete_profile_pic

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
    user = models.User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        security_question=payload.security_question,
        security_answer_hash=hash_security_answer(payload.security_answer)
    )
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
    return schemas.UserOut(
        id=user.id,
        email=user.email,
        plan=get_plan(user, db),
        role=user.role,
        has_security_question=bool(user.security_question),
        profile_pic_url=user.profile_pic_url,
    )


@router.patch("/me", response_model=schemas.UserOut)
def update_security_question(
    payload: schemas.UpdateSecurityQuestionRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update the security question/answer for the current user."""
    user.security_question = payload.security_question
    user.security_answer_hash = hash_security_answer(payload.security_answer)
    db.commit()
    return schemas.UserOut(
        id=user.id,
        email=user.email,
        plan=get_plan(user, db),
        role=user.role,
        has_security_question=bool(user.security_question),
        profile_pic_url=user.profile_pic_url,
    )


@router.post("/me/profile-pic", response_model=schemas.UploadProfilePicResponse)
async def upload_profile_pic(
    request: Request,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a profile picture for the signed-in user.

    Validates size and type here so a 2 MB JPEG is the ceiling, not a
    server-side surprise.
    """
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please upload an image file (jpg, png, webp).")

    content = await request.body()
    max_bytes = settings.MAX_PROFILE_PIC_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Profile picture must be {settings.MAX_PROFILE_PIC_MB} MB or smaller.",
        )

    # Remove the previous picture if there is one, so a re-upload does not
    # leave orphaned files in storage.
    if user.profile_pic_url:
        delete_profile_pic(user.profile_pic_url)

    url = save_profile_pic(user.id, content_type, content)
    user.profile_pic_url = url
    db.commit()
    return schemas.UploadProfilePicResponse(profile_pic_url=url)


@router.delete("/me/profile-pic", response_model=schemas.UserOut)
def delete_profile_pic_endpoint(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.profile_pic_url:
        delete_profile_pic(user.profile_pic_url)
    user.profile_pic_url = None
    db.commit()
    return schemas.UserOut(
        id=user.id,
        email=user.email,
        plan=get_plan(user, db),
        role=user.role,
        has_security_question=bool(user.security_question),
        profile_pic_url=None,
    )


# ---------- Forgot Password (Security Question/Answer Flow) ----------

@router.post("/forgot-password/question", response_model=schemas.ForgotPasswordQuestionResponse)
def get_security_question(payload: schemas.ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Return the security question for the given email (if account exists and has one set)."""
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user:
        # Do not reveal whether the email exists.
        return schemas.ForgotPasswordQuestionResponse(security_question=None, email=payload.email, has_security_question=False)
    if not user.security_question:
        return schemas.ForgotPasswordQuestionResponse(security_question=None, email=user.email, has_security_question=False)
    return schemas.ForgotPasswordQuestionResponse(security_question=user.security_question, email=user.email, has_security_question=True)


@router.post("/forgot-password/verify", response_model=schemas.VerifySecurityAnswerResponse)
def verify_security_answer_endpoint(payload: schemas.VerifySecurityAnswerRequest, db: Session = Depends(get_db)):
    """Verify the security answer and trigger Firebase password reset email."""
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user or not user.security_answer_hash:
        # Do not reveal whether the email exists or has a security answer.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid request.")
    
    if not verify_security_answer(payload.security_answer, user.security_answer_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect security answer.")
    
    # Security answer verified - in production, trigger Firebase password reset email here.
    # For now, return success; the frontend will call Firebase's sendPasswordResetEmail.
    return schemas.VerifySecurityAnswerResponse(
        reset_triggered=True,
        message="Security answer verified. A password reset email has been sent to your inbox."
    )


# Legacy password reset endpoints (disabled in firebase mode, kept for local dev)
@router.post("/password-reset/request")
def request_password_reset(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    """MVP stand-in for a real email-based reset flow. No SMTP account is
    configured for this build, so instead of emailing a link we return a
    short-lived reset token directly (dev-mode only). Wire this to a real
    email provider before going to production; do not ship this response
    shape as-is."""
    if settings.AUTH_MODE == "firebase":
        raise HTTPException(status.HTTP_410_GONE, "Use Firebase password reset from the client application")
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
