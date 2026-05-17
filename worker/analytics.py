# AI 리뷰 분석 — OpenAI로 감성 분석 / 키워드 추출 / 요약 후 ReviewAnalytics 저장
import json
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.db import _build_tenant_db_url
from app.models.tenant import Review, ReviewAnalytics

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

SYSTEM_PROMPT = """당신은 이커머스 리뷰 분석 AI입니다.
주어진 리뷰를 분석하여 반드시 아래 JSON 형식으로만 응답하세요.

{
  "sentiment": "positive" | "negative" | "neutral",
  "keywords": ["키워드1", "키워드2", "키워드3"],
  "summary": "한 문장 요약"
}"""


async def analyze_review(tenant_id: int, review_id: int) -> None:
    async with _worker_session(tenant_id) as db:
        result = await db.execute(select(Review).where(Review.id == review_id))
        review = result.scalar_one_or_none()

        if not review or not review.content:
            logger.warning("Review not found or empty — tenant=%s review_id=%s", tenant_id, review_id)
            return

        # 이미 분석된 경우 스킵
        existing = await db.execute(
            select(ReviewAnalytics).where(ReviewAnalytics.review_id == review_id)
        )
        if existing.scalar_one_or_none():
            logger.info("Already analyzed — tenant=%s review_id=%s", tenant_id, review_id)
            return

        sentiment, keywords, summary = await _call_openai(review.content)

        analytics = ReviewAnalytics(
            review_id=review_id,
            sentiment=sentiment,
            keywords=json.dumps(keywords, ensure_ascii=False),
            summary=summary,
        )
        db.add(analytics)
        await db.commit()

        logger.info(
            "Analytics saved — tenant=%s review_id=%s sentiment=%s",
            tenant_id, review_id, sentiment,
        )


async def get_unanalyzed_review_ids(tenant_id: int) -> list[int]:
    """analytics 레코드가 없는 리뷰 ID 목록을 반환한다."""
    from sqlalchemy import not_
    async with _worker_session(tenant_id) as db:
        analyzed_subq = select(ReviewAnalytics.review_id)
        result = await db.execute(
            select(Review.id).where(
                Review.content.isnot(None),
                not_(Review.id.in_(analyzed_subq)),
            )
        )
        return list(result.scalars().all())


async def _call_openai(content: str) -> tuple[str, list[str], str]:
    # OPENAI_API_KEY 미설정 시 개발 환경으로 간주하고 더미 데이터 반환
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — returning dummy analytics (dev mode)")
        return "neutral", ["테스트", "개발"], "개발 환경 더미 요약입니다."

    # Celery는 asyncio.run()으로 매 태스크마다 새 이벤트 루프를 사용하므로
    # AsyncOpenAI 클라이언트를 호출 시점에 생성해야 루프 불일치 에러가 없다.
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"리뷰: {content}"},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        sentiment = data.get("sentiment", "neutral")
        keywords = data.get("keywords", [])
        summary = data.get("summary", "")
        return sentiment, keywords, summary
    except Exception as e:
        logger.exception("OpenAI API error: %s", e)
        raise
