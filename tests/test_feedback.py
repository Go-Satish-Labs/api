"""
Tests for feedback intake.

The property worth protecting is scope: a user may read back only their own
messages, and the stored record must not become a way to smuggle dataset
content into a table with no retention policy attached.

The database is a throwaway SQLite file pinned by tests/conftest.py - setting
DATABASE_URL here would be too late, since the engine is already initialised.
"""
import pytest

from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _register(client, email: str) -> dict:
    response = client.post("/auth/register", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_feedback_is_saved_and_returned(client):
    headers = _register(client, "a@example.com")
    response = client.post("/feedback", headers=headers, json={
        "category": "bug", "rating": 2, "message": "The chart is empty", "context": "/datasets",
    })
    assert response.status_code == 201
    body = response.json()
    assert body["message"] == "The chart is empty"
    assert body["rating"] == 2
    assert body["status"] == "new"

    listed = client.get("/feedback", headers=headers)
    assert listed.status_code == 200
    assert [f["message"] for f in listed.json()] == ["The chart is empty"]


def test_feedback_requires_auth(client):
    assert client.get("/feedback").status_code in (401, 403)
    assert client.post("/feedback", json={"message": "hi"}).status_code in (401, 403)


def test_empty_message_is_rejected(client):
    headers = _register(client, "b@example.com")
    assert client.post("/feedback", headers=headers, json={"message": "   "}).status_code == 400


def test_oversized_message_is_rejected(client):
    headers = _register(client, "c@example.com")
    assert client.post("/feedback", headers=headers, json={"message": "x" * 5000}).status_code == 422


def test_out_of_range_rating_is_rejected(client):
    headers = _register(client, "d@example.com")
    assert client.post("/feedback", headers=headers, json={"message": "ok", "rating": 9}).status_code == 422
    assert client.post("/feedback", headers=headers, json={"message": "ok", "rating": 0}).status_code == 422


def test_a_user_only_sees_their_own_feedback(client):
    first = _register(client, "e@example.com")
    second = _register(client, "f@example.com")
    client.post("/feedback", headers=first, json={"message": "private note"})
    assert client.get("/feedback", headers=second).json() == []


def test_context_is_length_capped(client):
    """Context is free text from the client; it must not be able to grow
    without bound, and must not be treated as a place for file contents."""
    headers = _register(client, "g@example.com")
    response = client.post("/feedback", headers=headers, json={
        "message": "hi", "context": "x" * 5000,
    })
    assert response.status_code == 201
    # The response does not echo context, so assert via the stored row.
    from app.database import SessionLocal
    from app import models

    db = SessionLocal()
    try:
        stored = db.query(models.Feedback).order_by(models.Feedback.created_at.desc()).first()
        assert len(stored.context) <= 120
    finally:
        db.close()


def test_daily_limit_blocks_a_spam_run(client):
    headers = _register(client, "h@example.com")
    statuses = [
        client.post("/feedback", headers=headers, json={"message": f"message {i}"}).status_code
        for i in range(12)
    ]
    assert statuses[:10] == [201] * 10
    assert 429 in statuses


def test_deleting_a_user_removes_their_feedback(client):
    """Regression: feedback rows reference the workspace, so deleting a user
    used to cascade to the workspace while those rows still pointed at it and
    the database rejected the whole delete."""
    from app.database import SessionLocal
    from app import models

    headers = _register(client, "i@example.com")
    client.post("/feedback", headers=headers, json={"message": "goodbye"})

    email = "i@example.com"
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.email == email).first()
        assert user is not None
        user_id = user.id
        db.delete(user)
        db.commit()
        assert db.query(models.User).filter(models.User.id == user_id).first() is None
        # And nothing is left pointing at the removed workspace.
        assert db.query(models.Feedback).filter(
            models.Feedback.user_id == user_id
        ).count() == 0
    finally:
        db.close()
