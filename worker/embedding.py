# OpenAI text-embedding-3-small으로 리뷰 벡터 임베딩 생성 후 reviews 테이블 업데이트
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.db import _build_tenant_db_url
from app.models.tenant import Review

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _worker_session(tenant_id: int) -> AsyncGenerator[AsyncSession, None]:
    # Celery 워커는 asyncio.run()으로 매 태스크마다 새 이벤트 루프를 생성한다.
    # NullPool을 쓰면 루프 간 커넥션 재사용 없이 매번 새로 연결하므로 충돌이 없다.
    engine = create_async_engine(_build_tenant_db_url(tenant_id), poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def generate_embedding(tenant_id: int, review_id: int) -> None:
    async with _worker_session(tenant_id) as db:
        result = await db.execute(select(Review).where(Review.id == review_id))
        review = result.scalar_one_or_none()

        if not review or not review.content:
            logger.warning("Review not found or empty — tenant=%s review_id=%s", tenant_id, review_id)
            return

        if review.embedding is not None:
            logger.info("Embedding already exists — tenant=%s review_id=%s", tenant_id, review_id)
            return

        vector = await _call_embedding_api(review.content)
        review.embedding = vector
        await db.commit()

        logger.info("Embedding saved — tenant=%s review_id=%s", tenant_id, review_id)


async def _call_embedding_api(content: str) -> list[float]:
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — returning zero vector (dev mode)")
        return [0.0] * 1536

    # AsyncOpenAI 클라이언트를 호출 시점에 생성해야 이벤트 루프 불일치가 없다.
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    try:
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=content,
        )
        return response.data[0].embedding
    except Exception as e:
        logger.exception("OpenAI embedding API error: %s", e)
        raise
