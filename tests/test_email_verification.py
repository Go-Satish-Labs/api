"""
Tests for the email-verification gate and the account-claim path.

Two behaviours are being locked in here:

- an unverified Firebase token is refused, because the API is the only place
  that cannot be bypassed, and
- an existing account with the same address is claimed rather than duplicated,
  because users.email is UNIQUE and a blind insert told people their data
  "conflicts with existing data" when they were simply signing in again.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.deps import get_or_create_firebase_user


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path/'verify.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    yield db
    db.close(); engine.dispose()


# ── Token gate ────────────────────────────────────────────────────────────────

def test_unverified_token_is_refused(monkeypatch):
    from fastapi import HTTPException
    from app import firebase_auth

    monkeypatch.setattr(firebase_auth.settings, "FIREBASE_PROJECT_ID", "proj")
    monkeypatch.setattr(firebase_auth.settings, "REQUIRE_EMAIL_VERIFICATION", True)

    class FakeIdToken:
        @staticmethod
        def verify_firebase_token(token, request, project):
            return {"sub": "uid-1", "email": "a@example.com", "email_verified": False}

    import google.oauth2.id_token as real_module
    monkeypatch.setattr(real_module, "verify_firebase_token", FakeIdToken.verify_firebase_token)

    with pytest.raises(HTTPException) as exc:
        firebase_auth.verify_firebase_token("token")
    assert exc.value.status_code == 403
    # The message has to tell the person what to do, not just that they failed.
    assert "verify" in exc.value.detail.lower()


def test_verified_token_is_accepted(monkeypatch):
    from app import firebase_auth

    monkeypatch.setattr(firebase_auth.settings, "FIREBASE_PROJECT_ID", "proj")
    monkeypatch.setattr(firebase_auth.settings, "REQUIRE_EMAIL_VERIFICATION", True)

    import google.oauth2.id_token as real_module
    monkeypatch.setattr(
        real_module,
        "verify_firebase_token",
        lambda t, r, p: {"sub": "uid-2", "email": "b@example.com", "email_verified": True},
    )
    claims = firebase_auth.verify_firebase_token("token")
    assert claims["uid"] == "uid-2"
    assert claims["email_verified"] == "true"


def test_google_signin_is_not_blocked(monkeypatch):
    """Google and GitHub arrive already verified; holding them back would
    lock out everyone using a social provider."""
    from app import firebase_auth

    monkeypatch.setattr(firebase_auth.settings, "FIREBASE_PROJECT_ID", "proj")
    monkeypatch.setattr(firebase_auth.settings, "REQUIRE_EMAIL_VERIFICATION", True)

    import google.oauth2.id_token as real_module
    monkeypatch.setattr(
        real_module,
        "verify_firebase_token",
        lambda t, r, p: {"sub": "uid-3", "email": "c@example.com", "email_verified": True},
    )
    assert firebase_auth.verify_firebase_token("token")["uid"] == "uid-3"


def test_gate_can_be_turned_off(monkeypatch):
    from app import firebase_auth

    monkeypatch.setattr(firebase_auth.settings, "FIREBASE_PROJECT_ID", "proj")
    monkeypatch.setattr(firebase_auth.settings, "REQUIRE_EMAIL_VERIFICATION", False)

    import google.oauth2.id_token as real_module
    monkeypatch.setattr(
        real_module,
        "verify_firebase_token",
        lambda t, r, p: {"sub": "uid-4", "email": "d@example.com", "email_verified": False},
    )
    # Still lets the caller through - useful while iterating on a deployment.
    assert firebase_auth.verify_firebase_token("token")["uid"] == "uid-4"


# ── Claiming an existing account ──────────────────────────────────────────────

def test_existing_account_for_that_email_is_claimed(db_session):
    """The reported bug: a row already exists for the address, so inserting
    again violates users_email_key and the person is shown a conflict."""
    original = models.User(email="satish@example.com")
    db_session.add(original)
    db_session.commit()
    original_id = original.id

    user = get_or_create_firebase_user(db_session, "uid-new", "satish@example.com")

    assert user.id == original_id, "should have adopted the existing account"
    assert user.firebase_uid == "uid-new"
    assert db_session.query(models.User).filter_by(email="satish@example.com").count() == 1


def test_claiming_does_not_disturb_workspaces(db_session):
    """The existing account may already own a workspace and data; adopting it
    must not orphan any of that."""
    owner = models.User(email="has@example.com")
    db_session.add(owner)
    db_session.flush()
    db_session.add(models.Workspace(name="Theirs", owner_id=owner.id))
    db_session.commit()

    user = get_or_create_firebase_user(db_session, "uid-x", "has@example.com")
    assert db_session.query(models.Workspace).filter_by(owner_id=user.id).count() == 1


def test_a_genuinely_new_email_still_creates_everything(db_session):
    user = get_or_create_firebase_user(db_session, "uid-brand-new", "fresh@example.com")
    assert db_session.query(models.Workspace).filter_by(owner_id=user.id).count() == 1
    assert db_session.query(models.Subscription).filter_by(user_id=user.id).count() == 1