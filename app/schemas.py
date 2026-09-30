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
