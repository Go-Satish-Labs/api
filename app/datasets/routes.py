import json
import os

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..analytics.engine import (build_ai_fact_sheet, compute_metrics_and_dashboard,
                                 load_dataframe, profile_dataframe)
from ..config import settings
from ..database import get_db
from ..deps import get_current_user, get_current_workspace, get_plan, plan_limits
from ..storage.local_storage import delete_file, save_upload, workspace_storage_used_bytes

router = APIRouter(prefix="/datasets", tags=["datasets"])


def _validate_upload(file: UploadFile, content: bytes):
    """Blueprint section 12: validate file size, extension, MIME type, and
    content before processing. Treat uploaded files strictly as data."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                             f"Unsupported file type '{ext}'. Allowed: {settings.ALLOWED_EXTENSIONS}")
    if len(content) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                             f"File too large. Max allowed is {settings.MAX_UPLOAD_MB} MB")
    if len(content) == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty")
    # Basic content sniff: CSV should be decodable text; XLSX starts with the ZIP magic bytes.
    if ext == ".csv":
        try:
            content[:4096].decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            # Not fatal (could be a different encoding) but we flag it for the profiler.
            pass
    elif ext in (".xlsx", ".xls"):
        if content[:2] not in (b"PK", b"\xd0\xcf"):  # PK = xlsx(zip), D0CF = old xls (OLE)
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "File content does not match an Excel file")


@router.post("", response_model=schemas.DatasetOut, status_code=201)
async def upload_dataset(
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    ws: models.Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
):
    plan = get_plan(user, db)
    limits = plan_limits(plan)

    dataset_count = db.query(models.Dataset).filter(models.Dataset.workspace_id == ws.id).count()
    if dataset_count >= limits["max_datasets"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                             f"Dataset limit reached for the {plan} plan ({limits['max_datasets']}). Upgrade to add more.")

    content = await file.read()
    _validate_upload(file, content)

    current_mb = workspace_storage_used_bytes(ws.id) / (1024 * 1024)
    if current_mb + len(content) / (1024 * 1024) > limits["max_storage_mb"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                             f"Storage limit reached for the {plan} plan ({limits['max_storage_mb']} MB). Upgrade for more storage.")

    stored_path, size = save_upload(ws.id, file.filename, content)

    dataset = models.Dataset(
        workspace_id=ws.id,
        original_filename=file.filename,
        stored_path=stored_path,
        file_size_bytes=size,
        status="uploaded",
    )
    db.add(dataset)
    db.flush()

    try:
        df = load_dataframe(stored_path)
        profile = profile_dataframe(df)
        dataset.row_count = profile["row_count"]
        dataset.column_count = profile["column_count"]
        dataset.detected_category = profile["detected_category"]
        dataset.profile_json = json.dumps(profile)
        dataset.status = "profiled"

        dashboard_limit_ok = (
            db.query(models.Dashboard).filter(models.Dashboard.workspace_id == ws.id).count() < limits["max_dashboards"]
        )
        if dashboard_limit_ok:
            dashboard_config = compute_metrics_and_dashboard(df, profile)
            dashboard = models.Dashboard(
                workspace_id=ws.id,
                dataset_id=dataset.id,
                title=f"{file.filename} — Auto Dashboard",
                config_json=json.dumps(dashboard_config),
            )
            db.add(dashboard)
        dataset.status = "analyzed"
    except Exception as exc:  # noqa: BLE001 - MVP: surface a clean error, never crash the request
        dataset.status = "error"
        dataset.error_message = f"Could not process file: {exc}"

    db.commit()
    db.refresh(dataset)
    return dataset


@router.get("", response_model=list[schemas.DatasetOut])
def list_datasets(ws: models.Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    return db.query(models.Dataset).filter(models.Dataset.workspace_id == ws.id).order_by(models.Dataset.created_at.desc()).all()


@router.get("/{dataset_id}", response_model=schemas.DatasetProfileOut)
def get_dataset(dataset_id: str, ws: models.Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    dataset = _get_owned_dataset(dataset_id, ws, db)
    return schemas.DatasetProfileOut(dataset=dataset, profile=json.loads(dataset.profile_json or "{}"))


@router.get("/{dataset_id}/preview")
def preview_dataset(dataset_id: str, ws: models.Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    dataset = _get_owned_dataset(dataset_id, ws, db)
    df = load_dataframe(dataset.stored_path)
    preview = df.head(10).where(pd.notna(df.head(10)), None)
    return {
        "dataset_id": dataset.id,
        "original_filename": dataset.original_filename,
        "columns": list(preview.columns),
        "rows": preview.to_dict(orient="records"),
        "row_count": int(dataset.row_count or len(df)),
    }


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: str, ws: models.Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    """Blueprint acceptance test: 'Deleting a dataset removes associated
    stored data and metadata.'"""
    dataset = _get_owned_dataset(dataset_id, ws, db)
    delete_file(dataset.stored_path)
    db.delete(dataset)  # cascades to dashboards via relationship config
    db.commit()
    return None


def _get_owned_dataset(dataset_id: str, ws: models.Workspace, db: Session) -> models.Dataset:
    dataset = db.query(models.Dataset).filter(
        models.Dataset.id == dataset_id, models.Dataset.workspace_id == ws.id
    ).first()
    if not dataset:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found")
    return dataset
