import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from . import models  # noqa: F401 - registers every table on Base.metadata
from .ai.routes import router as ai_router
from .auth.routes import router as auth_router
from .billing.routes import router as billing_router
from .config import settings
from .dashboards.routes import router as dashboards_router
from .database import create_tables, database_status, init_engine
from .datasets.routes import router as datasets_router
from .retention import purge_expired_datasets, retention_notice
from .workspaces.routes import router as workspaces_router

DB_DOWN_DETAIL = (
    "The API cannot reach its database. Check DATABASE_URL in Brain/.env "
    "(see Brain/DATABASE.md)."
)

# How often the retention sweep runs. A deployment that sleeps for hours
# between requests still needs to delete on time, so this is a background task
# rather than something done opportunistically per request.
RETENTION_SWEEP_SECONDS = 3600


def _log(message: str) -> None:
    print(f"[startup] {message}", file=sys.stderr)


async def _retention_loop() -> None:
    """Periodically delete datasets past the retention window."""
    from .database import SessionLocal

    while True:
        try:
            await asyncio.sleep(RETENTION_SWEEP_SECONDS)
            db = SessionLocal()
            try:
                purge_expired_datasets(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed sweep must not kill the app
            _log(f"retention sweep failed: {type(exc).__name__}: {str(exc)[:200]}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Validate the database once, loudly, instead of failing per request."""
    status = init_engine()
    tables_ok, table_error = create_tables()
    _log(f"database: {status['mode']} -> {status['active_url']}")
    if status["connected"] and tables_ok:
        _log("database connection and schema are ready.")
        # Sweep once at boot so a server that was down past the deadline still
        # deletes on the way back up, rather than waiting a full interval.
        if status["connected"]:
            try:
                from .database import SessionLocal

                db = SessionLocal()
                try:
                    removed = purge_expired_datasets(db)
                    _log(f"retention: {settings.DATA_RETENTION_HOURS}h window, {removed} dataset(s) removed at boot")
                finally:
                    db.close()
            except Exception as exc:  # noqa: BLE001
                _log(f"retention boot sweep skipped: {type(exc).__name__}: {str(exc)[:200]}")
    else:
        _log(f"database NOT ready: {table_error or status['error']}")
        _log("the API keeps serving; DB-backed routes answer 503 until this is fixed.")

    task = asyncio.create_task(_retention_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(
    title="Lightweight AI Analytics API",
    description="Monolith backend: auth, workspaces, datasets, analytics engine, dashboards, AI Q&A, billing.",
    version="0.1.0",
    lifespan=lifespan,
)

def _allowed_origins() -> list[str]:
    """Normalise CORS_ORIGINS into the exact strings browsers send.

    Starlette matches ``Origin`` against this list with a plain string
    comparison, and browsers always send a scheme+host with no trailing
    slash. An env value pasted as ``https://app.example.com/`` therefore
    never matches and every preflight fails with a bare 400 that carries no
    ``Access-Control-Allow-Origin`` header - indistinguishable from a broken
    deploy. Stripping trailing slashes here makes both spellings work.
    """
    raw = [origin.strip() for origin in settings.CORS_ORIGINS.split(",")]
    return [origin.rstrip("/") for origin in raw if origin]


# In production, restrict this to the actual web/desktop/mobile client origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pool checkout timeouts (sqlalchemy.exc.TimeoutError) subclass SQLAlchemyError,
# so one handler covers "the database refused us" and "it never gave us a connection".
@app.exception_handler(SQLAlchemyError)
def handle_database_error(_request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """A dead database is an infrastructure state, not an application crash.

    Without this, SQLAlchemy/psycopg errors escape as a bare `500 text/plain`
    (21-byte body) that the client can only render as "something went wrong".
    """
    status = database_status()
    print(f"[database] request failed: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
    return JSONResponse(
        status_code=503,
        content={
            "detail": DB_DOWN_DETAIL,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            "database": {k: status[k] for k in ("mode", "active_url", "connected", "error", "fallback_used")},
        },
    )


app.include_router(auth_router)
app.include_router(workspaces_router)
app.include_router(datasets_router)
app.include_router(dashboards_router)
app.include_router(ai_router)
app.include_router(billing_router)


@app.get("/")
def root():
    return {
        "service": "lightweight-ai-analytics-api",
        "status": "ok",
        "payment_provider": settings.PAYMENT_PROVIDER,
    }


@app.get("/health")
def health():
    """Liveness + which database is actually serving requests right now."""
    status = database_status()
    return {
        "status": "healthy" if status["connected"] else "degraded",
        "payment_provider": settings.PAYMENT_PROVIDER,
        "database": {k: status[k] for k in ("mode", "active_url", "dialect", "connected", "fallback_used")},
    }


@app.get("/health/db")
def health_db():
    """Actively probes the active engine; use this after editing DATABASE_URL."""
    status = init_engine()
    return {
        "status": "healthy" if status["connected"] else "unhealthy",
        "database": status,
    }


@app.get("/privacy/retention")
def privacy_retention():
    """The retention window the server enforces and the copy describing it.

    Public and unauthenticated on purpose: the privacy claim has to be
    readable without an account, and it is generated from the same setting
    that drives the purge so the two can never disagree.
    """
    return retention_notice()

