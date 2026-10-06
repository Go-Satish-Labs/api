"""Engine/session wiring.

Two rules drive this module:

1. Never hang a request. Supabase Postgres sits behind a pooler, so a bad DSN
   used to block a worker for minutes and then surface as a raw ASGI 500. Every
   Postgres engine now gets a connect timeout, TCP keepalives, a pool checkout
   timeout and a pool recycle (Supabase drops idle connections after ~1 h).
2. Never leak credentials. The DSN is only ever logged with the password stripped.
"""
from __future__ import annotations

import sys
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

Base = declarative_base()


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def redact(url: str) -> str:
    """Return the DSN with its password replaced, safe for logs/health output."""
    try:
        parts = urlsplit(url)
        if not parts.password:
            return url
        host = parts.netloc.rsplit("@", 1)[-1]
        return urlunsplit(parts._replace(netloc=f"{parts.username or ''}:***@{host}"))
    except Exception:  # noqa: BLE001 - redacting must never be the thing that fails
        return "<unparseable database url>"


def _connect_args(url: str) -> dict:
    if _is_sqlite(url):
        return {"check_same_thread": False}
    # libpq/psycopg3 options: give up quickly, and stop NAT from killing idle conns.
    return {
        "connect_timeout": settings.DB_CONNECT_TIMEOUT_SECONDS,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }


def _build_engine(url: str) -> Engine:
    kwargs: dict = {"connect_args": _connect_args(url), "pool_pre_ping": True}
    if not _is_sqlite(url):
        kwargs.update(
            pool_size=5,
            max_overflow=5,
            pool_timeout=settings.DB_POOL_TIMEOUT_SECONDS,
            pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
        )
    return create_engine(url, **kwargs)


def _warn(message: str) -> None:
    print(f"[database] {message}", file=sys.stderr)


engine: Engine = _build_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Human-readable connection state, surfaced by /health and the 503 error body.
_status: dict = {
    "configured_url": redact(settings.DATABASE_URL),
    "active_url": redact(settings.DATABASE_URL),
    "dialect": engine.dialect.name,
    "mode": "primary",
    "fallback_enabled": bool(settings.DB_SQLITE_FALLBACK),
    "fallback_used": False,
    "connected": None,
    "error": None,
}


def get_engine() -> Engine:
    """Always go through this - init_engine() may swap the engine at startup."""
    return engine


def database_status() -> dict:
    return dict(_status)


def check_connection(target: Engine | None = None) -> str | None:
    """Return None when a trivial query succeeds, else a short error string."""
    target = target or engine
    try:
        with target.connect() as conn:
            conn.execute(text("SELECT 1"))
        return None
    except Exception as exc:  # noqa: BLE001 - any driver failure means "not usable"
        return f"{type(exc).__name__}: {str(exc).strip()[:400]}" or type(exc).__name__


def init_engine() -> dict:
    """Verify the configured database, and decide what to do if it is unreachable.

    Called once from the app lifespan. A broken primary database must never stop
    the process: with DB_SQLITE_FALLBACK on we serve from SQLite and shout about
    it, otherwise we keep the broken engine and every DB route answers 503.
    """
    global engine
    error = check_connection(engine)
    _status["connected"] = error is None
    _status["error"] = error
    if error is None:
        return database_status()

    _warn(f"primary DATABASE_URL is unusable -> {error}")
    can_fallback = (
        settings.DB_SQLITE_FALLBACK
        and _is_sqlite(settings.DB_SQLITE_FALLBACK_URL)
        and not _is_sqlite(settings.DATABASE_URL)
    )
    if not can_fallback:
        _warn(
            "no fallback available (DB_SQLITE_FALLBACK="
            f"{settings.DB_SQLITE_FALLBACK}); DB routes answer 503 until DATABASE_URL works."
        )
        return database_status()

    _warn(f"trying local SQLite fallback: {redact(settings.DB_SQLITE_FALLBACK_URL)}")
    try:
        candidate = _build_engine(settings.DB_SQLITE_FALLBACK_URL)
        fallback_error = check_connection(candidate)
        if fallback_error is not None:
            raise RuntimeError(fallback_error)
    except Exception as exc:  # noqa: BLE001
        _warn(f"SQLite fallback failed too ({exc}); keeping the primary engine")
        _status["connected"] = False
        _status["error"] = f"{error} | fallback: {exc}"
        return database_status()

    engine.dispose()
    engine = candidate
    SessionLocal.configure(bind=engine)
    _status.update(
        active_url=redact(settings.DB_SQLITE_FALLBACK_URL),
        dialect=engine.dialect.name,
        mode="sqlite-fallback",
        fallback_used=True,
        connected=True,
        error=error,  # keep the original failure visible in /health
    )
    _warn(
        "SERVING FROM LOCAL SQLITE - rows written now do NOT reach Supabase. "
        "Fix DATABASE_URL, then set DB_SQLITE_FALLBACK=false."
    )
    return database_status()


def _apply_idempotent_migrations() -> None:
    """Add columns that create_all cannot.

    Base.metadata.create_all only creates tables that do not exist; it never
    alters one that does. A column added to a model later therefore has to be
    added to the live table explicitly or every query against it fails. Each
    statement is written to be safe to run on every boot.
    """
    statements = [
        # Added for metered dataset quota: see Workspace.datasets_created.
        "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS "
        "datasets_created INTEGER NOT NULL DEFAULT 0",
        # Added for security question/answer on the User model.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "security_question VARCHAR",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "security_answer_hash VARCHAR",
        # Added for profile picture upload.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "profile_pic_url VARCHAR",
    ]
    with engine.begin() as conn:
        for statement in statements:
            try:
                conn.execute(text(statement))
            except Exception as exc:  # noqa: BLE001
                # A failed migration must not take the API down; the feature
                # that needs it degrades instead.
                print(
                    f"[database] migration skipped ({type(exc).__name__}: "
                    f"{str(exc)[:160]})",
                    file=sys.stderr,
                )


def create_tables() -> tuple[bool, str | None]:
    """Create any missing tables on the active engine."""
    try:
        Base.metadata.create_all(bind=engine)
        _apply_idempotent_migrations()
        return True, None
    except Exception as exc:  # noqa: BLE001
        message = f"{type(exc).__name__}: {str(exc).strip()[:400]}"
        _status["connected"] = False
        _status["error"] = message
        return False, message


def get_db():
    """FastAPI dependency: yields a DB session per-request and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

