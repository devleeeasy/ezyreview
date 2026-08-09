# AI 인용 모니터링 조회 API — 카테고리/브랜드 목록, 인용 결과 조회
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.auth import TenantData, verify_jwt
from app.core.db import get_tenant_session
from app.models.citation import Brand, Category, Citation
from app.models.citation import Query as CitationQueryModel

logger = logging.getLogger(__name__)

categories_router = APIRouter(prefix="/categories", tags=["citations"])
citations_router = APIRouter(prefix="/citations", tags=["citations"])


class BrandItem(BaseModel):
    """카테고리에 속한 브랜드 단건."""

    brand_id: int = Field(description="브랜드 고유 ID")
    name: str = Field(description="브랜드명")


class CategoryItem(BaseModel):
    """카테고리 단건과 소속 브랜드 목록."""

    category_id: int = Field(description="카테고리 고유 ID")
    name: str = Field(description="카테고리명")
    brands: list[BrandItem] = Field(description="이 카테고리에 속한 브랜드 목록")


class CategoryListResponse(BaseModel):
    """카테고리 목록 응답."""

    items: list[CategoryItem] = Field(description="카테고리 목록 (브랜드 포함)")


class CitationItem(BaseModel):
    """인용 결과 단건."""

    citation_id: int = Field(description="인용 레코드 고유 ID")
    query_id: int = Field(description="질의 ID")
    query_text: str = Field(description="AI 검색엔진에 던진 질의 텍스트")
    brand_id: int = Field(description="브랜드 ID")
    brand_name: str = Field(description="브랜드명")
    ai_source: str = Field(description="수집 대상 AI 검색엔진 (예: perplexica)")
    mentioned: bool = Field(description="답변에서 해당 브랜드가 언급되었는지 여부")
    mention_rank: int | None = Field(description="답변 내 최초 등장 순서 (1부터, 미언급 시 null)")
    context_snippet: str | None = Field(description="언급된 문맥 스니펫 (미언급 시 null)")
    sentiment: str | None = Field(description="문맥 감성 분류 — positive / negative / neutral (미분류 시 null)")
    collected_at: datetime = Field(description="수집 시각")


class CitationListResponse(BaseModel):
    """인용 결과 목록 응답."""

    total: int = Field(description="필터 조건에 해당하는 전체 인용 건수")
    items: list[CitationItem] = Field(description="현재 페이지 인용 결과 목록")


@categories_router.get(
    "",
    response_model=CategoryListResponse,
    summary="카테고리 및 브랜드 목록 조회",
    description="Phase 1 seed 카테고리와 각 카테고리에 속한 브랜드 목록을 반환합니다. JWT Bearer 토큰 인증 필요.",
)
async def list_categories(
    tenant: Annotated[TenantData, Depends(verify_jwt)],
) -> CategoryListResponse:
    async with get_tenant_session(tenant.id) as db:
        category_rows = await db.execute(select(Category).order_by(Category.id))
        categories = list(category_rows.scalars().all())

        brand_rows = await db.execute(select(Brand).order_by(Brand.id))
        brands = list(brand_rows.scalars().all())

    brands_by_category: dict[int, list[BrandItem]] = {}
    for b in brands:
        brands_by_category.setdefault(b.category_id, []).append(
            BrandItem(brand_id=b.id, name=b.name)
        )

    items = [
        CategoryItem(
            category_id=c.id, name=c.name, brands=brands_by_category.get(c.id, [])
        )
        for c in categories
    ]
    return CategoryListResponse(items=items)


@citations_router.get(
    "",
    response_model=CitationListResponse,
    summary="브랜드 인용 결과 조회",
    description=(
        "카테고리·브랜드·언급 여부·감성 필터를 적용해 인용 결과를 페이지네이션으로 반환합니다. "
        "JWT Bearer 토큰 인증 필요."
    ),
)
async def list_citations(
    tenant: Annotated[TenantData, Depends(verify_jwt)],
    category_id: int | None = Query(default=None, description="카테고리 ID로 필터"),
    brand_id: int | None = Query(default=None, description="브랜드 ID로 필터"),
    mentioned: bool | None = Query(default=None, description="언급 여부로 필터"),
    sentiment: str | None = Query(
        default=None,
        pattern="^(positive|negative|neutral)$",
        description="감성 필터 — positive / negative / neutral",
    ),
    limit: int = Query(default=20, ge=1, le=100, description="페이지당 결과 수 (최대 100)"),
    offset: int = Query(default=0, ge=0, description="건너뛸 결과 수 (페이지네이션)"),
) -> CitationListResponse:
    async with get_tenant_session(tenant.id) as db:
        base_query = (
            select(Citation, CitationQueryModel.text, Brand.name)
            .join(CitationQueryModel, Citation.query_id == CitationQueryModel.id)
            .join(Brand, Citation.brand_id == Brand.id)
        )
        if category_id is not None:
            base_query = base_query.where(CitationQueryModel.category_id == category_id)
        if brand_id is not None:
            base_query = base_query.where(Citation.brand_id == brand_id)
        if mentioned is not None:
            base_query = base_query.where(Citation.mentioned == mentioned)
        if sentiment is not None:
            base_query = base_query.where(Citation.sentiment == sentiment)

        total = await db.scalar(select(func.count()).select_from(base_query.subquery()))

        rows = await db.execute(
            base_query.order_by(Citation.collected_at.desc()).limit(limit).offset(offset)
        )

        items = [
            CitationItem(
                citation_id=citation.id,
                query_id=citation.query_id,
                query_text=query_text,
                brand_id=citation.brand_id,
                brand_name=brand_name,
                ai_source=citation.ai_source,
                mentioned=citation.mentioned,
                mention_rank=citation.mention_rank,
                context_snippet=citation.context_snippet,
                sentiment=citation.sentiment,
                collected_at=citation.collected_at,
            )
            for citation, query_text, brand_name in rows.all()
        ]

    return CitationListResponse(total=total or 0, items=items)
