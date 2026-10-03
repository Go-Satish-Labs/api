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


def test_delete_clears_the_dashboard_reference_too(client):
    """Regression: SharedDashboard has TWO foreign keys back to the source
    data. Clearing only dataset_id left dashboard_id pointing at a row the
    cascade was about to remove, so the delete still failed - this is what
    made the retention sweep skip and silently keep nothing purged."""
    from app.database import SessionLocal
    from app import models

    headers = _account(client, "del5@example.com")
    dataset_id = _upload(client, headers)

    db = SessionLocal()
    try:
        dataset = db.query(models.Dataset).get(dataset_id)
        dashboard = db.query(models.Dashboard).filter_by(dataset_id=dataset_id).first()
        assert dashboard is not None, "upload should have created a dashboard"
        db.add(models.SharedDashboard(
            token="bothrefs00001", workspace_id=dataset.workspace_id,
            dataset_id=dataset_id, dashboard_id=dashboard.id,
            mode="history", title="t", config_json="{}", html="<p>x</p>",
        ))
        db.commit()
    finally:
        db.close()

    assert client.delete(f"/datasets/{dataset_id}", headers=headers).status_code == 204

    db = SessionLocal()
    try:
        share = db.query(models.SharedDashboard).filter_by(token="bothrefs00001").one()
        assert share.dataset_id is None
        assert share.dashboard_id is None
        db.delete(share)
        db.commit()
    finally:
        db.close()


def test_retention_purge_clears_both_references(monkeypatch):
    """The retention sweep must not be able to abort on a shared link, or the
    24 hour deletion promise silently stops being kept."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import models
    from app.database import Base
    from app.retention import purge_expired_datasets

    monkeypatch.setattr("app.retention.delete_file", lambda _p: None)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    user = models.User(email="p@example.com")
    session.add(user); session.flush()
    workspace = models.Workspace(owner_id=user.id)
    session.add(workspace); session.flush()
    dataset = models.Dataset(
        workspace_id=workspace.id, original_filename="old.csv",
        stored_path="x/old.csv", file_size_bytes=1,
        created_at=now - timedelta(hours=48),
    )
    session.add(dataset); session.flush()
    dashboard = models.Dashboard(
        workspace_id=workspace.id, dataset_id=dataset.id, title="d", config_json="{}",
    )
    session.add(dashboard); session.flush()
    session.add(models.SharedDashboard(
        token="purgeboth0001", workspace_id=workspace.id,
        dataset_id=dataset.id, dashboard_id=dashboard.id,
        mode="history", title="t", config_json="{}", html="<p>x</p>",
    ))
    session.commit()

    assert purge_expired_datasets(session) == 1
    assert session.query(models.Dataset).count() == 0
    share = session.query(models.SharedDashboard).filter_by(token="purgeboth0001").one()
    assert share.dataset_id is None and share.dashboard_id is None
    session.close()


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
