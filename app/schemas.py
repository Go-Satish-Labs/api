from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field


# ---------- Auth ----------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    plan: str
    role: str = "member"
    class Config:
        from_attributes = True


# ---------- Workspace ----------
class WorkspaceOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    class Config:
        from_attributes = True


# ---------- Dataset ----------
class DatasetOut(BaseModel):
    id: str
    original_filename: str
    file_size_bytes: int
    row_count: int
    column_count: int
    detected_category: str
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    # Hours until the file is automatically deleted, so the UI can state it
    # per row instead of only in a one-time notice.
    hours_until_deletion: Optional[float] = None
    class Config:
        from_attributes = True


class DatasetProfileOut(BaseModel):
    dataset: DatasetOut
    profile: dict[str, Any]


# ---------- Dashboard ----------
class DashboardOut(BaseModel):
    id: str
    dataset_id: str
    title: str
    config: dict[str, Any]
    created_at: datetime


# ---------- AI ----------
class AskQuestionRequest(BaseModel):
    dataset_id: str
    question: str = Field(min_length=1, max_length=500)


class AskQuestionResponse(BaseModel):
    answer: str
    used_facts: dict[str, Any]
    disclaimer: str
    remaining_ai_questions: int
    # How the question was understood, so the UI can show "you asked about X".
    interpretation: Optional[dict[str, Any]] = None
    # Present when the question needed a forward projection: history + forecast
    # series the frontend can plot, plus confidence and method.
    forecast: Optional[dict[str, Any]] = None
    engine: str = "deterministic"


# ---------- Prediction ----------
class PredictRequest(BaseModel):
    """A user-configured prediction run from the dashboard's Prediction tab."""
    dataset_id: str
    # What to predict. None with mode="clustering" groups rows instead.
    target: Optional[str] = None
    # Columns the model learns from. Empty means "use everything else".
    features: list[str] = Field(default_factory=list)
    # auto | classification | regression | clustering
    mode: str = "auto"
    n_clusters: Optional[int] = Field(default=None, ge=2, le=8)


class PredictOptionsResponse(BaseModel):
    is_labeled: bool
    suggested_target: Optional[str] = None
    suggested_mode: str = "auto"
    date_column: Optional[str] = None
    numeric_columns: list[str]
    categorical_columns: list[str]
    clusterable: bool
    summary: str
    hint: str


# ---------- Feedback ----------
class FeedbackCreate(BaseModel):
    category: str = "general"
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    message: str = Field(min_length=1, max_length=4000)
    # The page the user was on. A route name, not a filename.
    context: Optional[str] = None


class FeedbackOut(BaseModel):
    id: str
    category: str
    rating: Optional[int] = None
    message: str
    status: str
    created_at: datetime
    class Config:
        from_attributes = True


# ---------- Sharing ----------
class ShareDashboardRequest(BaseModel):
    dataset_id: str
    # history = KPIs/charts/quality; prediction = the user's chosen model
    mode: str = "history"
    title: Optional[str] = None
    # Required when mode is "prediction": the already-computed result, so the
    # server never silently regenerates a different model than the one shown.
    prediction: Optional[dict[str, Any]] = None


# ---------- Billing ----------
class CreateOrderRequest(BaseModel):
    plan: str = "premium"


class CreateOrderResponse(BaseModel):
    provider: str
    order_id: str
    amount: int
    currency: str
    checkout_hint: str


class ConfirmPaymentRequest(BaseModel):
    order_id: str
    payment_id: str
    signature: Optional[str] = None


class SubscriptionOut(BaseModel):
    plan: str
    status: str
    provider: str
    current_period_end: Optional[datetime] = None
