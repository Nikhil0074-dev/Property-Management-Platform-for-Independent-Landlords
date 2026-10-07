import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import models  # noqa: F401  (register tables)
from .config import settings
from .database import Base, engine
from .routers import auth, core, leases, maintenance, misc, rent


@asynccontextmanager
async def lifespan(_):
    Base.metadata.create_all(engine)  # use Alembic migrations for production schema changes
    yield


app = FastAPI(title="Property Management Platform", version="1.0.0", lifespan=lifespan)
if settings.CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

for r in (auth, core, leases, rent, maintenance, misc):
    app.include_router(r.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


FRONTEND = Path(os.getenv("FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend"))
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
