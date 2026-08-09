# AI 인용 수집 오케스트레이션 — 활성 질의를 수집·파싱해 citations 테이블에 저장
#
# response_raw에는 Vane의 원본 NDJSON(질의당 약 1~2MB)이 아니라 재구성된 최종
# 답변 텍스트를 저장한다. 같은 질의에 브랜드마다 citation row가 하나씩 생기는
# 구조라, 원본을 그대로 넣으면 브랜드 수만큼 중복 저장되어 용량 낭비가 크고
# 어차피 원본은 재파싱 전엔 사람이 읽기도 어렵기 때문.
import asyncio
import logging
import random
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.ai_answer_collector import TENANT_ID, collect_answer
from app.collectors.citation_parser import BrandRef, parse_citations
from app.core.db import get_tenant_session
from app.models.citation import Brand, BrandAlias, Citation, Query
from app.models.tenant import now_kst
from worker.citation_notify import notify_google_chat

logger = logging.getLogger(__name__)

AI_SOURCE = "perplexica"


@dataclass
class CollectionResult:
    saved_count: int = 0
    failed_query_ids: list[int] = field(default_factory=list)


async def _load_brand_refs(db: AsyncSession, category_id: int) -> list[BrandRef]:
    result = await db.execute(select(Brand).where(Brand.category_id == category_id))
    brands = list(result.scalars().all())

    alias_result = await db.execute(
        select(BrandAlias).where(BrandAlias.brand_id.in_([b.id for b in brands]))
    )
    aliases_by_brand: dict[int, list[str]] = {}
    for alias in alias_result.scalars().all():
        aliases_by_brand.setdefault(alias.brand_id, []).append(alias.alias)

    return [
        BrandRef(brand_id=b.id, names=[b.name] + aliases_by_brand.get(b.id, []))
        for b in brands
    ]


async def _collect_and_save_for_query(tenant_id: int, query: Query) -> int | None:
    """질의 1건을 수집·파싱해 저장한다. 실패 시 None, 성공 시 저장된 row 수를 반환."""
    try:
        answer = await collect_answer(query.id, query.text)
    except Exception:
        logger.exception("수집 실패 — query_id=%d", query.id)
        return None

    if not answer.answer_text:
        logger.warning("답변 재구성 실패 — query_id=%d, citations 저장 스킵", query.id)
        return None

    async with get_tenant_session(tenant_id) as db:
        brand_refs = await _load_brand_refs(db, query.category_id)
        parsed = parse_citations(answer.answer_text, brand_refs)

        db.add_all(
            [
                Citation(
                    tenant_id=tenant_id,
                    query_id=query.id,
                    brand_id=p.brand_id,
                    ai_source=AI_SOURCE,
                    response_raw=answer.answer_text,
                    mentioned=p.mentioned,
                    mention_rank=p.mention_rank,
                    context_snippet=p.context_snippet,
                    collected_at=answer.collected_at,
                )
                for p in parsed
            ]
        )
        await db.commit()

    logger.info(
        "citations 저장 완료 — query_id=%d, brands=%d, mentioned=%d",
        query.id, len(parsed), sum(p.mentioned for p in parsed),
    )
    return len(parsed)


async def _collect_and_save_for_queries(tenant_id: int, queries: list[Query]) -> CollectionResult:
    result = CollectionResult()
    for i, query in enumerate(queries):
        if i > 0:
            await asyncio.sleep(random.uniform(3, 8))

        saved = await _collect_and_save_for_query(tenant_id, query)
        if saved is None:
            result.failed_query_ids.append(query.id)
        else:
            result.saved_count += saved

    return result


async def collect_and_save_citations(tenant_id: int = TENANT_ID) -> CollectionResult:
    """활성 질의 전체를 수집·파싱해 citations에 저장한다."""
    async with get_tenant_session(tenant_id) as db:
        result = await db.execute(select(Query).where(Query.active == True))  # noqa: E712
        queries = list(result.scalars().all())

    return await _collect_and_save_for_queries(tenant_id, queries)


async def backfill_missing_queries(tenant_id: int = TENANT_ID) -> CollectionResult:
    """오늘(KST) citation이 하나도 수집되지 않은 active 질의만 재수집한다.
    (수집 실패 재처리 — 성공한 질의는 하루 한 번이면 충분하므로 건드리지 않는다.)"""
    today_start = now_kst().replace(hour=0, minute=0, second=0, microsecond=0)

    async with get_tenant_session(tenant_id) as db:
        collected_today = select(Citation.query_id).where(Citation.collected_at >= today_start)
        result = await db.execute(
            select(Query).where(
                Query.active == True,  # noqa: E712
                Query.id.not_in(collected_today),
            )
        )
        missing_queries = list(result.scalars().all())

    if not missing_queries:
        logger.info("backfill 대상 없음 — 오늘 모든 active 질의 수집 완료")
        return CollectionResult()

    logger.info("backfill 대상 — %d개 질의", len(missing_queries))
    return await _collect_and_save_for_queries(tenant_id, missing_queries)


async def run_daily_collection(tenant_id: int = TENANT_ID) -> CollectionResult:
    """전체 수집 → 실패분 1회 backfill → 결과 요약 Google Chat 알림까지 수행한다."""
    result = await collect_and_save_citations(tenant_id)

    if result.failed_query_ids:
        backfill_result = await backfill_missing_queries(tenant_id)
        result.saved_count += backfill_result.saved_count
        result.failed_query_ids = backfill_result.failed_query_ids

    if result.failed_query_ids:
        message = (
            f"[EzyReview] AI 인용 수집 완료 (tenant={tenant_id}) — "
            f"저장 {result.saved_count}건, 실패 질의 {len(result.failed_query_ids)}건: "
            f"{result.failed_query_ids}"
        )
    else:
        message = f"[EzyReview] AI 인용 수집 완료 (tenant={tenant_id}) — 저장 {result.saved_count}건, 실패 없음"

    await notify_google_chat(message)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    final_result = asyncio.run(run_daily_collection())
    print(f"저장된 citation row 수: {final_result.saved_count}, 실패 질의: {final_result.failed_query_ids}")
