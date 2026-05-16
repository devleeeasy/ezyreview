# ezyreview FastAPI 앱 진입점
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

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


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
