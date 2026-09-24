import uuid
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer,
                         String, Text)
from sqlalchemy.orm import relationship

from .database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    """A Firebase-authenticated account and its server-side authorization data."""
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=gen_uuid)
    email = Column(String, unique=True, index=True, nullable=False)
    firebase_uid = Column(String, unique=True, index=True, nullable=True)
    role = Column(String, nullable=False, default="member")
    # Retained temporarily for legacy local-test accounts; Firebase users never use it.
    hashed_password = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    workspaces = relationship("Workspace", back_populates="owner", cascade="all, delete-orphan")
    subscription = relationship("Subscription", back_populates="user", uselist=False, cascade="all, delete-orphan")


class Workspace(Base):
    """Every dataset/dashboard/usage-record belongs to exactly one workspace.
    This is the tenant-isolation boundary required by the blueprint (section 5)."""
    __tablename__ = "workspaces"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False, default="My Workspace")
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="workspaces")
    datasets = relationship("Dataset", back_populates="workspace", cascade="all, delete-orphan")
    dashboards = relationship("Dashboard", back_populates="workspace", cascade="all, delete-orphan")
    usage_logs = relationship("UsageLog", back_populates="workspace", cascade="all, delete-orphan")


class Dataset(Base):
    """Metadata about an uploaded CSV/XLSX file. The raw file lives on disk
    (local_storage) / object storage; only metadata + profile summary sit in the DB."""
    __tablename__ = "datasets"

    id = Column(String, primary_key=True, default=gen_uuid)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False)
    original_filename = Column(String, nullable=False)
    stored_path = Column(String, nullable=False)
    file_size_bytes = Column(Integer, nullable=False)
    row_count = Column(Integer, default=0)
    column_count = Column(Integer, default=0)
    detected_category = Column(String, default="generic")
    profile_json = Column(Text, default="{}")  # column types, missing values, outliers, etc.
    status = Column(String, default="uploaded")  # uploaded -> profiled -> analyzed -> error
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    workspace = relationship("Workspace", back_populates="datasets")
    dashboards = relationship("Dashboard", back_populates="dataset", cascade="all, delete-orphan")


class Dashboard(Base):
    """A generated dashboard configuration (KPI cards + chart specs) as JSON,
    so the frontend renders it consistently (blueprint section 8)."""
    __tablename__ = "dashboards"

    id = Column(String, primary_key=True, default=gen_uuid)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False)
    dataset_id = Column(String, ForeignKey("datasets.id"), nullable=False)
    title = Column(String, default="Auto Dashboard")
    config_json = Column(Text, nullable=False)  # KPI cards, charts, tables spec
    created_at = Column(DateTime, default=datetime.utcnow)

    workspace = relationship("Workspace", back_populates="dashboards")
    dataset = relationship("Dataset", back_populates="dashboards")


class Subscription(Base):
    """Server-side source of truth for plan/entitlements. Clients NEVER decide
    this themselves (blueprint section 11)."""
    __tablename__ = "subscriptions"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), unique=True, nullable=False)
    plan = Column(String, default="free")  # free | premium
    status = Column(String, default="active")  # active | cancelled | past_due
    provider = Column(String, default="mock")  # mock | razorpay
    provider_order_id = Column(String, nullable=True)
    provider_payment_id = Column(String, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="subscription")


class UsageLog(Base):
    """Tracks AI question usage (and could track other metered actions) per
    workspace per month, for quota enforcement (blueprint section 9 & 11)."""
    __tablename__ = "usage_logs"

    id = Column(String, primary_key=True, default=gen_uuid)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False)
    action = Column(String, nullable=False)  # e.g. "ai_question"
    period = Column(String, nullable=False)  # "YYYY-MM"
    count = Column(Integer, default=0)

    workspace = relationship("Workspace", back_populates="usage_logs")
