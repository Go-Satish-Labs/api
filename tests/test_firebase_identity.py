"""
Tests for the lazy Firebase account creation in deps.get_or_create_firebase_user.

The client fires several authenticated requests at once on first load, so this
runs on every sign-in and has to survive being entered twice at the same
moment. users.firebase_uid is UNIQUE, which makes the race a guaranteed
IntegrityError for whoever loses - the whole point of the test is that losing
is not a failure.
"""
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.deps import get_or_create_firebase_user


@pytest.fixture
def session_factory(tmp_path):
    # A file, not :memory: - every thread gets its own connection, and an
    # in-memory database is per-connection, so two threads would see two
    # different empty databases and never collide at all.
    engine = create_engine(
        f"sqlite:///{tmp_path/'ident.db'}", connect_args={"check_same_thread": False, "timeout": 15}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine), engine


def test_creates_user_workspace_and_subscription(session_factory):
    factory, engine = session_factory
    db = factory()
    user = get_or_create_firebase_user(db, "uid-1", "new@example.com")
    assert user.id
    assert db.query(models.Workspace).filter_by(owner_id=user.id).count() == 1
    assert db.query(models.Subscription).filter_by(user_id=user.id).count() == 1
    db.close(); engine.dispose()


def test_second_call_reuses_the_same_account(session_factory):
    factory, engine = session_factory
    db = factory()
    first = get_or_create_firebase_user(db, "uid-2", "again@example.com")
    second = get_or_create_firebase_user(db, "uid-2", "again@example.com")
    assert first.id == second.id
    assert db.query(models.Workspace).filter_by(owner_id=first.id).count() == 1
    db.close(); engine.dispose()


def test_email_change_is_applied_not_duplicated(session_factory):
    factory, engine = session_factory
    db = factory()
    get_or_create_firebase_user(db, "uid-3", "old@example.com")
    updated = get_or_create_firebase_user(db, "uid-3", "new@example.com")
    assert updated.email == "new@example.com"
    assert db.query(models.User).filter_by(firebase_uid="uid-3").count() == 1
    db.close(); engine.dispose()


def test_concurrent_first_requests_both_succeed(session_factory):
    """The regression: two requests, one account, no error for either.

    Without the retry the loser raised IntegrityError and the user saw "That
    change conflicts with existing data" for a sign-in that had worked.
    """
    factory, engine = session_factory
    outcomes: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(2)

    def attempt() -> None:
        db = factory()
        try:
            start.wait(timeout=5)
            user = get_or_create_firebase_user(db, "uid-race", "race@example.com")
            with lock:
                outcomes.append(f"ok:{user.id}")
        except Exception as exc:  # noqa: BLE001
            with lock:
                outcomes.append(f"{type(exc).__name__}")
        finally:
            db.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    db = factory()
    try:
        users = db.query(models.User).filter_by(firebase_uid="uid-race").all()
        workspaces = db.query(models.Workspace).filter_by(owner_id=users[0].id).all() if users else []
        assert len(outcomes) == 2
        assert all(o.startswith("ok:") for o in outcomes), f"one request failed: {outcomes}"
        # Exactly one account, one workspace, one subscription - the unique
        # constraint did the deduplication, the retry kept the losers quiet.
        assert len(users) == 1
        assert len(workspaces) == 1
        assert db.query(models.Subscription).filter_by(user_id=users[0].id).count() == 1
    finally:
        db.close(); engine.dispose()
