# AI 인용 수집 오케스트레이션 — 활성 질의를 수집·파싱해 citations 테이블에 저장
#
# response_raw에는 Vane의 원본 NDJSON(질의당 약 1~2MB)이 아니라 재구성된 최종
# 답변 텍스트를 저장한다. 같은 질의에 브랜드마다 citation row가 하나씩 생기는
# 구조라, 원본을 그대로 넣으면 브랜드 수만큼 중복 저장되어 용량 낭비가 크고
# 어차피 원본은 재파싱 전엔 사람이 읽기도 어렵기 때문.
import asyncio
import logging
import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.ai_answer_collector import TENANT_ID, collect_answer
from app.collectors.citation_parser import BrandRef, parse_citations
from app.core.db import get_tenant_session
from app.models.citation import Brand, BrandAlias, Citation, Query

logger = logging.getLogger(__name__)

AI_SOURCE = "perplexica"


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


async def collect_and_save_citations(tenant_id: int = TENANT_ID) -> int:
    """활성 질의 전체를 수집·파싱해 citations에 저장. 저장된 row 수를 반환."""
    async with get_tenant_session(tenant_id) as db:
        result = await db.execute(select(Query).where(Query.active == True))  # noqa: E712
        queries = list(result.scalars().all())

    saved_count = 0
    for i, query in enumerate(queries):
        if i > 0:
            await asyncio.sleep(random.uniform(3, 8))

        try:
            answer = await collect_answer(query.id, query.text)
        except Exception:
            logger.exception("수집 실패 — query_id=%d", query.id)
            continue

        if not answer.answer_text:
            logger.warning("답변 재구성 실패 — query_id=%d, citations 저장 스킵", query.id)
            continue

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

        saved_count += len(parsed)
        logger.info(
            "citations 저장 완료 — query_id=%d, brands=%d, mentioned=%d",
            query.id, len(parsed), sum(p.mentioned for p in parsed),
        )

    return saved_count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = asyncio.run(collect_and_save_citations())
    print(f"저장된 citation row 수: {count}")
