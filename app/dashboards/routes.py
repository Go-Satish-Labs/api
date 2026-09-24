import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models
from ..analytics.engine import compute_metrics_and_dashboard, load_dataframe, profile_dataframe
from ..database import get_db
from ..deps import get_current_workspace

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


def _refresh_dashboard_if_needed(dashboard: models.Dashboard, db: Session) -> dict:
    config = json.loads(dashboard.config_json)
    if config.get("charts"):
        return config

    dataset = dashboard.dataset
    if not dataset or not dataset.stored_path:
        return config

    df = load_dataframe(dataset.stored_path)
    profile = profile_dataframe(df)
    config = compute_metrics_and_dashboard(df, profile)
    dashboard.config_json = json.dumps(config)
    db.commit()
    return config


@router.get("")
def list_dashboards(ws: models.Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    dashboards = db.query(models.Dashboard).filter(models.Dashboard.workspace_id == ws.id).all()
    return [
        {"id": d.id, "dataset_id": d.dataset_id, "title": d.title, "created_at": d.created_at}
        for d in dashboards
    ]


@router.get("/{dashboard_id}")
def get_dashboard(dashboard_id: str, ws: models.Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    dashboard = db.query(models.Dashboard).filter(
        models.Dashboard.id == dashboard_id, models.Dashboard.workspace_id == ws.id
    ).first()
    if not dashboard:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dashboard not found")
    return {
        "id": dashboard.id,
        "dataset_id": dashboard.dataset_id,
        "title": dashboard.title,
        "config": _refresh_dashboard_if_needed(dashboard, db),
        "created_at": dashboard.created_at,
    }


@router.get("/by-dataset/{dataset_id}")
def get_dashboard_by_dataset(dataset_id: str, ws: models.Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    dashboard = db.query(models.Dashboard).filter(
        models.Dashboard.dataset_id == dataset_id, models.Dashboard.workspace_id == ws.id
    ).first()
    if not dashboard:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No dashboard generated for this dataset yet")
    return {
        "id": dashboard.id,
        "dataset_id": dashboard.dataset_id,
        "title": dashboard.title,
        "config": _refresh_dashboard_if_needed(dashboard, db),
        "created_at": dashboard.created_at,
    }
