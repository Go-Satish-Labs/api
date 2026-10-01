import csv
import io
import json
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from .. import models, schemas
from ..analytics.engine import (compute_metrics_and_dashboard, load_dataframe,
                                 prediction_options, profile_dataframe,
                                 run_targeted_prediction)
from ..config import settings
from ..database import get_db
from ..deps import get_current_workspace
from ..storage.local_storage import get_file_url
from .html_report import render_dashboard_html

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


def _refresh_dashboard_if_needed(dashboard: models.Dashboard, db: Session) -> dict:
    config = json.loads(dashboard.config_json)
    if config.get("charts"):
        return config

    dataset = dashboard.dataset
    if not dataset or not dataset.stored_path:
        return config

    # load_dataframe reads a URL, not a storage key - passing stored_path
    # raised "No such file or directory" and made every share fail.
    df = load_dataframe(get_file_url(dataset.stored_path))
    profile = profile_dataframe(df)
    config = compute_metrics_and_dashboard(df, profile)
    dashboard.config_json = json.dumps(config)
    db.commit()
    return config


def _owned_dataset(dataset_id: str, ws: models.Workspace, db: Session) -> models.Dataset:
    dataset = db.query(models.Dataset).filter(
        models.Dataset.id == dataset_id, models.Dataset.workspace_id == ws.id
    ).first()
    if not dataset:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found")
    return dataset


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


@router.post("/share")
def share_dashboard(
    payload: schemas.ShareDashboardRequest,
    ws: models.Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
):
    """Render a dashboard to standalone HTML, store it, and return a share link.

    The stored document is self-contained and holds only computed aggregates,
    so the link keeps working after the uploaded file is deleted on schedule
    and cannot be used to reach the source rows.
    """
    dataset = _owned_dataset(payload.dataset_id, ws, db)
    dashboard = db.query(models.Dashboard).filter(
        models.Dashboard.dataset_id == dataset.id,
        models.Dashboard.workspace_id == ws.id,
    ).first()

    if payload.mode == "prediction":
        if not payload.prediction:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "A prediction result is required to share a prediction dashboard")
        prediction = payload.prediction
    else:
        if not dashboard:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No dashboard generated for this dataset yet")
        config = _refresh_dashboard_if_needed(dashboard, db)
        prediction = None

    config = _refresh_dashboard_if_needed(dashboard, db) if dashboard else {}
    html = render_dashboard_html(
        config,
        title=payload.title or dashboard.title if dashboard else f"{dataset.original_filename} - Prediction",
        mode=payload.mode,
        prediction=prediction,
        dataset_filename=dataset.original_filename,
    )

    share = models.SharedDashboard(
        token=secrets.token_urlsafe(16),
        workspace_id=ws.id,
        dataset_id=dataset.id,
        dashboard_id=dashboard.id if dashboard else None,
        mode=payload.mode,
        title=payload.title or (dashboard.title if dashboard else "Shared dashboard"),
        config_json=json.dumps({"mode": payload.mode}),
        html=html,
        expires_at=datetime.utcnow() + timedelta(hours=settings.SHARE_LINK_EXPIRY_HOURS),
    )
    db.add(share)
    db.commit()

    return {
        "token": share.token,
        "url": f"/dashboards/shared/{share.token}",
        "mode": share.mode,
        "title": share.title,
        "expires_at": share.expires_at,
        "view_count": 0,
    }


@router.get("/shared/list")
def list_shared(ws: models.Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    rows = (
        db.query(models.SharedDashboard)
        .filter(models.SharedDashboard.workspace_id == ws.id)
        .order_by(models.SharedDashboard.created_at.desc())
        .all()
    )
    return [
        {
            "token": r.token, "mode": r.mode, "title": r.title,
            "url": f"/dashboards/shared/{r.token}",
            "created_at": r.created_at, "expires_at": r.expires_at,
            "view_count": r.view_count,
            "expired": bool(r.expires_at and r.expires_at < datetime.utcnow()),
        }
        for r in rows
    ]


@router.delete("/shared/{token}", status_code=204)
def revoke_shared(token: str, ws: models.Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    share = db.query(models.SharedDashboard).filter(
        models.SharedDashboard.token == token,
        models.SharedDashboard.workspace_id == ws.id,
    ).first()
    if not share:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share link not found")
    db.delete(share)
    db.commit()
    return None


@router.get("/shared/{token}", response_class=HTMLResponse)
def view_shared(token: str, request: Request, db: Session = Depends(get_db)):
    """Serve a shared dashboard.

    Deliberately unauthenticated: the unguessable token *is* the credential,
    which is what makes the link pasteable into a chat or an email. Access is
    still scoped - an expired or unknown token 404s rather than leaking that
    a token ever existed.
    """
    share = db.query(models.SharedDashboard).filter(
        models.SharedDashboard.token == token
    ).first()
    if not share:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This share link does not exist or has been revoked")
    if share.expires_at and share.expires_at < datetime.utcnow():
        raise HTTPException(status.HTTP_410_GONE, "This share link has expired")

    share.view_count = (share.view_count or 0) + 1
    db.commit()

    return HTMLResponse(
        content=share.html,
        headers={
            # The report is a static document with no scripts; these headers
            # stop a browser from treating a stored copy as live app state.
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get("/predict-options/{dataset_id}", response_model=schemas.PredictOptionsResponse)
def predict_options_route(dataset_id: str, ws: models.Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    """Which columns the Prediction tab should offer as a target and as inputs."""
    dataset = _owned_dataset(dataset_id, ws, db)
    profile = json.loads(dataset.profile_json or "{}")
    if not profile:
        raise HTTPException(status.HTTP_409_CONFLICT, "This dataset hasn't been profiled yet")
    return prediction_options(profile)


@router.post("/predict")
def predict(payload: schemas.PredictRequest, ws: models.Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    """Run the prediction the user configured in the dashboard's Prediction tab.

    A bad request (unusable target, too few rows) comes back as a 200 with an
    `error` string rather than an HTTP error, so the UI can show the reason
    next to the controls the user needs to change.
    """
    dataset = _owned_dataset(payload.dataset_id, ws, db)
    profile = json.loads(dataset.profile_json or "{}")
    if not profile:
        raise HTTPException(status.HTTP_409_CONFLICT, "This dataset hasn't been profiled yet")

    known = {c["name"] for c in profile.get("columns", [])}
    unknown = [f for f in payload.features if f not in known]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown column(s): {', '.join(unknown)}")
    if payload.target and payload.target not in known:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown target column: {payload.target}")
    if payload.target in payload.features:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The column being predicted can't also be an input")

    df = load_dataframe(get_file_url(dataset.stored_path))
    result = run_targeted_prediction(
        df, profile,
        target=payload.target,
        features=payload.features or None,
        mode=payload.mode,
        n_clusters=payload.n_clusters,
    )
    return {"prediction": result, "dataset_id": dataset.id}


@router.get("/by-dataset/{dataset_id}/export")
def export_dashboard(dataset_id: str, fmt: str = "json", ws: models.Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    """Export dashboard data. fmt = json | csv | html"""
    dashboard = db.query(models.Dashboard).filter(
        models.Dashboard.dataset_id == dataset_id, models.Dashboard.workspace_id == ws.id
    ).first()
    if not dashboard:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No dashboard found")

    config = _refresh_dashboard_if_needed(dashboard, db)
    title = dashboard.title

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Section", "Metric", "Value"])
        for kpi in config.get("kpi_cards", []):
            writer.writerow(["KPI", kpi["metric"], kpi.get("sum", kpi.get("average", ""))])
        for chart in config.get("charts", []):
            for row in chart.get("data", []):
                writer.writerow([chart["title"], row.get("x", ""), row.get("y", "")])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="dashboard.csv"'},
        )

    if fmt == "html":
        html = render_dashboard_html(
            config, title=title, mode="history",
            prediction=config.get("predictions"),
            dataset_filename=dashboard.dataset.original_filename if dashboard.dataset else "",
        )
        return Response(
            content=html, media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="dashboard.html"'},
        )

    # default: json
    return Response(
        content=json.dumps(config, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="dashboard.json"'},
    )
