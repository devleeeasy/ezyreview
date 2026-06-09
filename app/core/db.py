# 테넌트 DB 동적 라우팅 — API 키에서 추출한 tenant_id로 해당 DB 세션 반환
import logging
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.main import MainBase
from app.models.tenant import TenantBase

logger = logging.getLogger(__name__)

_main_engine = create_async_engine(settings.DATABASE_URL, echo=False)
_main_session_factory = async_sessionmaker(_main_engine, expire_on_commit=False)

# 테넌트 엔진 캐시 — 테넌트별로 한 번만 생성 후 재사용
_tenant_engines: dict[int, AsyncEngine] = {}


def _build_tenant_db_url(tenant_id: int) -> str:
    return re.sub(r"/[^/]+$", f"/tenant_{tenant_id}_db", settings.DATABASE_URL)


def get_tenant_engine(tenant_id: int) -> AsyncEngine:
    if tenant_id not in _tenant_engines:
        _tenant_engines[tenant_id] = create_async_engine(
            _build_tenant_db_url(tenant_id), echo=False
        )
    return _tenant_engines[tenant_id]


async def create_tenant_db(tenant_id: int) -> None:
    """테넌트 DB가 없으면 생성하고 테이블을 초기화한다."""
    db_name = f"tenant_{tenant_id}_db"

    # CREATE DATABASE는 트랜잭션 밖에서 실행해야 하므로 asyncpg로 직접 연결
    conn = await asyncpg.connect(dsn=settings.POSTGRES_MAINTENANCE_URL)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
            logger.info("Created tenant DB: %s", db_name)
    finally:
        await conn.close()

    engine = get_tenant_engine(tenant_id)
    async with engine.begin() as conn:
        # create_all 전에 vector extension 등록 필수
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(TenantBase.metadata.create_all)
        # 기존 테넌트 DB에 embedding 컬럼 추가 (create_all은 기존 테이블을 수정하지 않음)
        await conn.execute(
            text("ALTER TABLE reviews ADD COLUMN IF NOT EXISTS embedding vector(1536)")
        )
    logger.info("Tables ready in %s", db_name)


@asynccontextmanager
async def get_tenant_session(tenant_id: int) -> AsyncGenerator[AsyncSession, None]:
    engine = get_tenant_engine(tenant_id)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


async def get_main_db() -> AsyncGenerator[AsyncSession, None]:
    async with _main_session_factory() as session:
        yield session


async def init_main_db() -> None:
    async with _main_engine.begin() as conn:
        await conn.run_sync(MainBase.metadata.create_all)
    logger.info("main_db tables initialized")
