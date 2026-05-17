# ezyreview FastAPI 앱 진입점
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.reviews import router as reviews_router
from app.api.insights import router as insights_router
from app.api.tenants import router as tenants_router
from app.api.webhook import router as webhook_router
from app.core.db import init_main_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — initializing main_db tables")
    await init_main_db()
    logger.info("main_db ready")
    yield
    logger.info("Shutting down")


app = FastAPI(title="ezyreview", version="1.0.0", lifespan=lifespan)

app.include_router(tenants_router)
app.include_router(webhook_router)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(reviews_router)
app.include_router(insights_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
