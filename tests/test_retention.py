"""
Tests for data retention.

The point of these is that the number shown to a user and the number the
server deletes on are the same number. A privacy notice that drifts from
enforced behaviour is worse than no notice, so the purge window and the
displayed copy are asserted to come from one setting.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.retention import (hours_remaining, purge_expired_datasets,
                           retention_notice)


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway SQLite session with one workspace, user and dataset."""
    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    user = models.User(email="u@example.com")
    session.add(user); session.flush()
    workspace = models.Workspace(owner_id=user.id)
    session.add(workspace); session.flush()

    # The purge compares against the real clock when no time is passed in, so
    # the fixture's ages have to be relative to the real now.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    fresh = models.Dataset(
        workspace_id=workspace.id, original_filename="fresh.csv",
        stored_path="x/fresh.csv", file_size_bytes=10,
        created_at=now - timedelta(hours=1),
    )
    stale = models.Dataset(
        workspace_id=workspace.id, original_filename="stale.csv",
        stored_path="x/stale.csv", file_size_bytes=10,
        created_at=now - timedelta(hours=48),
    )
    session.add_all([fresh, stale]); session.commit()

    deleted: list[str] = []
    monkeypatch.setattr("app.retention.delete_file", lambda p: deleted.append(p))
    session.info = {"deleted_files": deleted}
    yield session
    session.close()


def test_purge_removes_only_datasets_past_the_window(db):
    removed = purge_expired_datasets(db)
    assert removed == 1
    remaining = db.query(models.Dataset).all()
    assert [d.original_filename for d in remaining] == ["fresh.csv"]


def test_purge_deletes_the_stored_file(db):
    purge_expired_datasets(db)
    assert db.info["deleted_files"] == ["x/stale.csv"]


def test_purge_is_idempotent(db):
    assert purge_expired_datasets(db) == 1
    assert purge_expired_datasets(db) == 0


def test_purge_removes_the_dataset_even_if_the_file_delete_fails(db, monkeypatch):
    def boom(_path):
        raise OSError("storage unavailable")
    monkeypatch.setattr("app.retention.delete_file", boom)
    removed = purge_expired_datasets(db)
    # The row must not survive a storage failure, or the metadata would
    # outlive the promise that the data is gone.
    assert removed == 1
    assert [d.original_filename for d in db.query(models.Dataset).all()] == ["fresh.csv"]


def test_purge_cascades_to_dashboards(db):
    dataset = db.query(models.Dataset).filter_by(original_filename="stale.csv").one()
    db.add(models.Dashboard(workspace_id=dataset.workspace_id, dataset_id=dataset.id,
                            title="d", config_json="{}"))
    db.commit()
    purge_expired_datasets(db)
    assert db.query(models.Dashboard).count() == 0


def test_notice_reports_the_configured_window(monkeypatch):
    monkeypatch.setattr("app.retention.settings.DATA_RETENTION_HOURS", 12)
    notice = retention_notice()
    assert notice["retention_hours"] == 12
    assert "12 hours" in notice["message"]
    assert "12 hours" in notice["short"]


def test_notice_never_disagrees_with_the_purge(monkeypatch):
    """The copy is generated from the setting the purge reads."""
    for hours in (6, 12, 24, 48):
        monkeypatch.setattr("app.retention.settings.DATA_RETENTION_HOURS", hours)
        assert f"{hours} hours" in retention_notice()["message"]


def test_hours_remaining_counts_down_and_floors_at_zero():
    now = datetime(2026, 1, 2, 0, 0, 0)
    assert hours_remaining(now - timedelta(hours=1), now) == pytest.approx(23.0, abs=0.01)
    assert hours_remaining(now - timedelta(hours=99), now) == 0.0
    assert hours_remaining(None, now) is None
