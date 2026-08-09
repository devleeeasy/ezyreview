# 인용 문맥 분석 오케스트레이션 — 감성 미분류 citation 분류, 임베딩 없는 query 임베딩 생성,
# pgvector 코사인 유사도 기반 유사 질의 조회
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.citation_context import classify_context_sentiment, embed_query_text
from app.collectors.ai_answer_collector import TENANT_ID
from app.core.db import get_tenant_session
from app.models.citation import Citation, Query

logger = logging.getLogger(__name__)


async def classify_pending_citations(tenant_id: int = TENANT_ID) -> int:
    """mentioned=True이고 아직 sentiment가 없는 citation을 감성 분류해 저장. 처리 건수 반환."""
    async with get_tenant_session(tenant_id) as db:
        result = await db.execute(
            select(Citation).where(Citation.mentioned == True, Citation.sentiment.is_(None))  # noqa: E712
        )
        citations = list(result.scalars().all())

    classified = 0
    for citation in citations:
        if not citation.context_snippet:
            continue
        try:
            sentiment = await classify_context_sentiment(citation.context_snippet)
        except Exception:
            logger.exception("감성 분류 실패 — citation_id=%d", citation.id)
            continue

        async with get_tenant_session(tenant_id) as db:
            row = await db.get(Citation, citation.id)
            row.sentiment = sentiment
            await db.commit()

        classified += 1
        logger.info("감성 분류 완료 — citation_id=%d, sentiment=%s", citation.id, sentiment)

    return classified


async def embed_pending_queries(tenant_id: int = TENANT_ID) -> int:
    """임베딩이 없는 active query에 임베딩을 생성해 저장. 처리 건수 반환."""
    async with get_tenant_session(tenant_id) as db:
        result = await db.execute(
            select(Query).where(Query.active == True, Query.embedding.is_(None))  # noqa: E712
        )
        queries = list(result.scalars().all())

    embedded = 0
    for query in queries:
        try:
            vector = await embed_query_text(query.text)
        except Exception:
            logger.exception("임베딩 생성 실패 — query_id=%d", query.id)
            continue

        async with get_tenant_session(tenant_id) as db:
            row = await db.get(Query, query.id)
            row.embedding = vector
            await db.commit()

        embedded += 1
        logger.info("임베딩 저장 완료 — query_id=%d", query.id)

    return embedded


async def find_similar_queries(
    db: AsyncSession, query_id: int, limit: int = 5, min_similarity: float = 0.8
) -> list[tuple[int, str, float]]:
    """주어진 질의와 pgvector 코사인 유사도 기준 가장 비슷한 질의들을 반환한다.
    질의 자신은 제외. 카테고리 스코프 한정이 필요하면 호출부에서 결과를 추가 필터링한다."""
    sql = sql_text("""
        SELECT q2.id, q2.text,
               ROUND(CAST(1 - (q1.embedding <=> q2.embedding) AS numeric), 3) AS similarity
        FROM queries q1
        JOIN queries q2 ON q2.id != q1.id AND q2.embedding IS NOT NULL
        WHERE q1.id = :query_id AND q1.embedding IS NOT NULL
        ORDER BY q1.embedding <=> q2.embedding
        LIMIT :limit
    """)
    rows = (await db.execute(sql, {"query_id": query_id, "limit": limit})).all()
    return [
        (row.id, row.text, float(row.similarity))
        for row in rows
        if row.similarity >= min_similarity
    ]


if __name__ == "__main__":

    async def _run() -> None:
        classified = await classify_pending_citations()
        print(f"감성 분류 완료: {classified}건")

        embedded = await embed_pending_queries()
        print(f"임베딩 생성 완료: {embedded}건")

        async with get_tenant_session(TENANT_ID) as db:
            result = await db.execute(select(Query).where(Query.active == True))  # noqa: E712
            queries = list(result.scalars().all())

            for q in queries[:3]:
                similar = await find_similar_queries(db, q.id, limit=3, min_similarity=0.0)
                print(f"\n[{q.id}] {q.text}")
                for sim_id, sim_text, score in similar:
                    print(f"  ~{score} [{sim_id}] {sim_text}")

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())
