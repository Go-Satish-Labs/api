from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models
from .ai.routes import router as ai_router
from .auth.routes import router as auth_router
from .billing.routes import router as billing_router
from .config import settings
from .dashboards.routes import router as dashboards_router
from .database import Base, engine
from .datasets.routes import router as datasets_router
from .workspaces.routes import router as workspaces_router

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Lightweight AI Analytics API",
    description="Monolith backend: auth, workspaces, datasets, analytics engine, dashboards, AI Q&A, billing.",
    version="0.1.0",
)

# In production, restrict this to the actual web/desktop/mobile client origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    return {"status": "healthy"}
