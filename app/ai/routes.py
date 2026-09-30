import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..analytics.engine import build_ai_fact_sheet, load_dataframe
from ..database import get_db
from ..deps import get_current_user, get_current_workspace, get_plan, plan_limits
from ..storage.local_storage import get_file_url
from .service import answer_question, is_unsafe_question

router = APIRouter(prefix="/ai", tags=["ai"])


def _current_period() -> str:
    now = datetime.utcnow()
    return f"{now.year}-{now.month:02d}"


@router.post("/ask", response_model=schemas.AskQuestionResponse)
def ask_your_data(
    payload: schemas.AskQuestionRequest,
    user: models.User = Depends(get_current_user),
    ws: models.Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
):
    if is_unsafe_question(payload.question):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                             "This question can't be processed. Please ask about your dataset's metrics instead.")

    plan = get_plan(user, db)
    limits = plan_limits(plan)
    period = _current_period()

    usage = db.query(models.UsageLog).filter(
        models.UsageLog.workspace_id == ws.id,
        models.UsageLog.action == "ai_question",
        models.UsageLog.period == period,
    ).first()
    used_count = usage.count if usage else 0
    if used_count >= limits["max_ai_questions"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                             f"Monthly AI question limit reached for the {plan} plan "
                             f"({limits['max_ai_questions']}/month). Upgrade for more.")

    dataset = db.query(models.Dataset).filter(
        models.Dataset.id == payload.dataset_id, models.Dataset.workspace_id == ws.id
    ).first()
    if not dataset:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found")

    dashboard = db.query(models.Dashboard).filter(
        models.Dashboard.dataset_id == dataset.id,
        models.Dashboard.workspace_id == ws.id,
    ).first()
    if not dashboard:
        raise HTTPException(status.HTTP_409_CONFLICT, "This dataset hasn't finished analysis yet")

    profile = json.loads(dataset.profile_json or "{}")
    dashboard_config = json.loads(dashboard.config_json)
    fact_sheet = build_ai_fact_sheet(profile, dashboard_config)

    # Lazy: the service only calls this for forecast/trend questions, so a
    # "what is the total revenue" question never re-reads the file.
    def _load():
        return load_dataframe(get_file_url(dataset.stored_path))

    result = answer_question(payload.question, fact_sheet, df=_load)

    if usage:
        usage.count += 1
    else:
        usage = models.UsageLog(workspace_id=ws.id, action="ai_question", period=period, count=1)
        db.add(usage)
    db.commit()

    remaining = max(0, limits["max_ai_questions"] - (used_count + 1))
    return schemas.AskQuestionResponse(
        answer=result["answer"], used_facts=result["used_facts"],
        disclaimer=result["disclaimer"], remaining_ai_questions=remaining,
        interpretation=result.get("interpretation"),
        forecast=result.get("forecast"),
        engine=result.get("engine", "deterministic"),
    )
