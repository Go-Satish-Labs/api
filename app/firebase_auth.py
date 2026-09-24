"""Firebase token verification kept separate from routing and persistence."""
from fastapi import HTTPException, status

from .config import settings


def verify_firebase_token(token: str) -> dict[str, str]:
    if not settings.FIREBASE_PROJECT_ID:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Firebase authentication is not configured")
    try:
        from google.auth.transport import requests
        from google.oauth2 import id_token
        claims = id_token.verify_firebase_token(token, requests.Request(), settings.FIREBASE_PROJECT_ID)
    except ImportError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Firebase token verifier is not installed") from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired Firebase session") from exc
    uid = claims.get("user_id") or claims.get("uid") or claims.get("sub")
    email = claims.get("email")
    if not uid or not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Firebase session has no verified email")
    return {"uid": uid, "email": email.lower()}
