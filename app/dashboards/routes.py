import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from .. import models, schemas
from ..analytics.engine import (compute_metrics_and_dashboard, load_dataframe,
                                 prediction_options, profile_dataframe,
                                 run_targeted_prediction)
from ..database import get_db
from ..deps import get_current_workspace
from ..storage.local_storage import get_file_url

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
        kpi_rows = "".join(
            f"<tr><td>{k['metric']}</td><td>{k.get('sum','')}</td><td>{k.get('average','')}</td></tr>"
            for k in config.get("kpi_cards", [])
        )
        chart_sections = "".join(
            f"<h3>{c['title']}</h3><table border='1'><tr><th>X</th><th>Y</th></tr>"
            + "".join(f"<tr><td>{r.get('x','')}</td><td>{r.get('y','')}</td></tr>" for r in c.get("data", []))
            + "</table>"
            for c in config.get("charts", []) if c.get("data")
        )
        html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>{title}</title>
<style>body{{font-family:sans-serif;padding:24px}}table{{border-collapse:collapse;margin-bottom:24px}}td,th{{padding:6px 12px;border:1px solid #ddd}}</style>
</head><body>
<h1>{title}</h1>
<h2>KPI Summary</h2>
<table><tr><th>Metric</th><th>Total</th><th>Average</th></tr>{kpi_rows}</table>
{chart_sections}
</body></html>"""
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
