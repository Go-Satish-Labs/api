from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_workspace, get_current_user, get_plan, plan_limits
from ..storage.local_storage import workspace_storage_used_bytes

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get("/me", response_model=schemas.WorkspaceOut)
def my_workspace(ws: models.Workspace = Depends(get_current_workspace)):
    return ws


@router.get("/usage")
def usage(
    ws: models.Workspace = Depends(get_current_workspace),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Shows current usage vs plan limits, so the frontend can render the
    'usage limits and upgrade CTA without disrupting the dashboard' (section 3).

    `datasets` is metered uploads, not files currently present: deleting a
    dataset does not return its slot to the quota. `current_datasets` is the
    number actually stored, which the UI can show alongside so the difference
    is explained rather than looking like a bug.
    """
    plan = get_plan(user, db)
    limits = plan_limits(plan)
    dataset_count = db.query(models.Dataset).filter(models.Dataset.workspace_id == ws.id).count()
    dashboard_count = db.query(models.Dashboard).filter(models.Dashboard.workspace_id == ws.id).count()
    storage_used_mb = round(workspace_storage_used_bytes(ws.id) / (1024 * 1024), 2)

    # Metered uploads, not current rows. A dataset the user deleted still
    # counts, so the limit cannot be reset by deleting files - otherwise a
    # capped user could upload, delete and upload again indefinitely.
    # Self-heals upwards for workspaces created before this counter existed,
    # where the only record of past uploads is the rows still present.
    if (ws.datasets_created or 0) < dataset_count:
        ws.datasets_created = dataset_count
        db.commit()
        db.refresh(ws)

    return {
        "plan": plan,
        "limits": limits,
        "usage": {
            "datasets": ws.datasets_created or 0,
            "current_datasets": dataset_count,
            "dashboards": dashboard_count,
            "storage_mb": storage_used_mb,
        },
    }
