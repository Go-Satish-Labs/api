"""
Tests for deleting a dataset.

The delete endpoint was the one place a foreign key could still be violated:
SharedDashboard points at datasets.id, and deleting a dataset that had ever
been shared raised IntegrityError, which the global handler reported as a 503
claiming the database was unreachable. These lock in the two behaviours that
makes a delete succeed: the link is detached rather than cascaded, and the
error handler no longer blames the connection for a constraint violation.
"""
import os

import pytest

os.environ.setdefault("AUTH_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _account(client, email: str) -> dict:
    r = client.post("/auth/register", json={"email": email, "password": "password123"})
    if r.status_code != 201:
        r = client.post("/auth/login", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _upload(client, headers, rows="A,B\n1,2\n3,4\n"):
    return client.post(
        "/datasets",
        files={"file": ("t.csv", rows, "text/csv")},
        headers=headers,
    ).json()["id"]


def test_delete_plain_dataset(client):
    headers = _account(client, "del1@example.com")
    dataset_id = _upload(client, headers)
    assert client.delete(f"/datasets/{dataset_id}", headers=headers).status_code == 204
    assert client.get("/datasets", headers=headers).json() == []


def test_delete_succeeds_when_a_link_was_created(client):
    """The regression: a shared report still referenced the dataset."""
    from app.database import SessionLocal
    from app import models

    headers = _account(client, "del2@example.com")
    dataset_id = _upload(client, headers)

    db = SessionLocal()
    try:
        dataset = db.query(models.Dataset).get(dataset_id)
        share = models.SharedDashboard(
            token="regressiontok123",
            workspace_id=dataset.workspace_id,
            dataset_id=dataset_id,
            mode="history",
            title="t",
            config_json="{}",
            html="<p>x</p>",
        )
        db.add(share)
        db.commit()
        token = share.token
    finally:
        db.close()

    response = client.delete(f"/datasets/{dataset_id}", headers=headers)
    assert response.status_code == 204, response.text

    db = SessionLocal()
    try:
        assert db.query(models.Dataset).get(dataset_id) is None
        # The link survives, detached: a shared report holds aggregates only
        # and is meant to keep working once the file is gone.
        share = db.query(models.SharedDashboard).filter_by(token=token).one()
        assert share.dataset_id is None
        db.delete(share)
        db.commit()
    finally:
        db.close()


def test_shared_link_still_serves_after_its_dataset_is_gone(client):
    from app.database import SessionLocal
    from app import models

    headers = _account(client, "del3@example.com")
    dataset_id = _upload(client, headers)

    db = SessionLocal()
    try:
        dataset = db.query(models.Dataset).get(dataset_id)
        db.add(models.SharedDashboard(
            token="servedafterdel1", workspace_id=dataset.workspace_id,
            dataset_id=dataset_id, mode="history", title="t",
            config_json="{}", html="<html><body>report</body></html>",
        ))
        db.commit()
    finally:
        db.close()

    client.delete(f"/datasets/{dataset_id}", headers=headers)
    assert client.get("/dashboards/shared/servedafterdel1").status_code == 200

    db = SessionLocal()
    try:
        db.query(models.SharedDashboard).filter_by(token="servedafterdel1").delete()
        db.commit()
    finally:
        db.close()


def test_integrity_error_is_not_reported_as_a_dead_database(client):
    """A constraint violation means the database worked. Reporting it as
    503 'cannot reach its database' sends readers to inspect DATABASE_URL."""
    from app.database import SessionLocal
    from app import models

    headers = _account(client, "del4@example.com")
    dataset_id = _upload(client, headers)

    db = SessionLocal()
    try:
        dataset = db.query(models.Dataset).get(dataset_id)
        # A duplicate token violates the unique constraint on insert.
        for token in ("dupconstraint1", "dupconstraint1"):
            db.add(models.SharedDashboard(
                token=token, workspace_id=dataset.workspace_id,
                dataset_id=dataset_id, mode="history", title="t",
                config_json="{}", html="<p>x</p>",
            ))
        with pytest.raises(Exception):
            db.commit()
        db.rollback()
    finally:
        db.close()
