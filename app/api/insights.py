# 인사이트 API — 리뷰 감성 요약, 목록 조회, 의미 기반 벡터 검색
import json
import logging
import random
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from app.core.auth import TenantData, verify_jwt
from app.core.config import settings
from app.core.db import get_tenant_session
from app.models.tenant import Review, ReviewAnalytics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/insights", tags=["insights"])


class SentimentCount(BaseModel):
    """AI 감성 분석 결과 분포."""

    positive: int = Field(description="긍정 리뷰 수")
    negative: int = Field(description="부정 리뷰 수")
    neutral: int = Field(description="중립 리뷰 수")
    unanalyzed: int = Field(description="아직 AI 분석이 완료되지 않은 리뷰 수")


class SummaryResponse(BaseModel):
    """리뷰 인사이트 요약. 전체 통계, 감성 분포, 상위 키워드를 반환합니다."""

    total_reviews: int = Field(description="전체 리뷰 수")
    avg_rating: float | None = Field(description="평균 평점 (리뷰 없으면 null)")
    sentiment: SentimentCount = Field(description="감성 분포")
    top_keywords: list[str] = Field(description="전체 리뷰에서 가장 많이 언급된 상위 3개 키워드 (빈도 내림차순)")


class ReviewItem(BaseModel):
    """리뷰 단건 정보. AI 분석이 완료되지 않은 경우 sentiment / keywords / summary는 null입니다."""

    review_id: int = Field(description="리뷰 고유 ID")
    order_id: str = Field(description="연결된 주문 ID")
    content: str | None = Field(description="리뷰 내용")
    rating: float | None = Field(description="평점 (1.0 ~ 5.0)")
    sentiment: str | None = Field(description="AI 감성 분석 결과 — positive / negative / neutral")
    keywords: list[str] = Field(description="AI가 추출한 핵심 키워드 목록")
    summary: str | None = Field(description="AI가 생성한 한 줄 요약")


class ReviewListResponse(BaseModel):
    """리뷰 목록 응답. sentiment 필터와 limit / offset 페이지네이션을 지원합니다."""

    total: int = Field(description="필터 조건에 해당하는 전체 리뷰 수")
    items: list[ReviewItem] = Field(description="현재 페이지 리뷰 목록")


class SearchResultItem(BaseModel):
    """의미 기반 검색 결과 단건."""

    review_id: int = Field(description="리뷰 고유 ID")
    content: str | None = Field(description="리뷰 내용")
    rating: float | None = Field(description="평점 (1.0 ~ 5.0)")
    sentiment: str | None = Field(description="AI 감성 분석 결과 — positive / negative / neutral")
    similarity_score: float = Field(description="검색어와의 코사인 유사도 (0~1, 높을수록 유사)")
    created_at: datetime = Field(description="리뷰 등록 시각")


async def _embed_query(q: str) -> list[float]:
    """검색어를 text-embedding-3-small 벡터로 변환. API 키 없으면 dev 모드 랜덤 벡터 반환."""
    if not settings.OPENAI_API_KEY:
        return [random.uniform(-1.0, 1.0) for _ in range(1536)]
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    response = await client.embeddings.create(model="text-embedding-3-small", input=q)
    return response.data[0].embedding


@router.get(
    "/summary",
    response_model=SummaryResponse,
    summary="리뷰 인사이트 요약",
    description=(
        "전체 리뷰 수, 평균 평점, 감성 분포(긍정/부정/중립/미분석), "
        "상위 3개 키워드를 반환합니다. "
        "키워드는 AI 분석 결과에서 언급 빈도 기준으로 집계됩니다. "
        "JWT Bearer 토큰 인증 필요."
    ),
)
async def get_summary(
    tenant: Annotated[TenantData, Depends(verify_jwt)],
) -> SummaryResponse:
    async with get_tenant_session(tenant.id) as db:
        # 쿼리 1: 리뷰 전체 수 + 평균 평점
        row = await db.execute(
            select(func.count(Review.id), func.avg(Review.rating)).select_from(Review)
        )
        total, avg_rating = row.one()

        # 쿼리 2: 감성별 분포 한 번에 (GROUP BY)
        rows = await db.execute(
            select(ReviewAnalytics.sentiment, func.count())
            .group_by(ReviewAnalytics.sentiment)
        )
        sentiment_counts: dict[str, int] = {"positive": 0, "negative": 0, "neutral": 0}
        analyzed_count = 0
        for sentiment, count in rows.all():
            if sentiment in sentiment_counts:
                sentiment_counts[sentiment] = count
            analyzed_count += count

        unanalyzed = max((total or 0) - analyzed_count, 0)

        # 쿼리 3: 키워드 빈도 집계 — PostgreSQL json_array_elements_text()로 DB 내 언네스팅
        keyword_rows = await db.execute(
            text("""
                SELECT keyword, COUNT(*) AS cnt
                FROM review_analytics,
                     json_array_elements_text(keywords::json) AS keyword
                WHERE keywords IS NOT NULL
                  AND keywords != 'null'
                GROUP BY keyword
                ORDER BY cnt DESC
                LIMIT 3
            """)
        )
        top_keywords: list[str] = [row.keyword for row in keyword_rows.all()]

    return SummaryResponse(
        total_reviews=total or 0,
        avg_rating=round(float(avg_rating), 2) if avg_rating else None,
        sentiment=SentimentCount(
            positive=sentiment_counts["positive"],
            negative=sentiment_counts["negative"],
            neutral=sentiment_counts["neutral"],
            unanalyzed=unanalyzed,
        ),
        top_keywords=top_keywords,
    )


@router.get(
    "/reviews",
    response_model=ReviewListResponse,
    summary="리뷰 목록 조회",
    description="수집된 리뷰와 AI 분석 결과를 페이지네이션으로 반환합니다. sentiment 필터로 감성별 조회가 가능합니다.",
)
async def get_reviews(
    tenant: Annotated[TenantData, Depends(verify_jwt)],
    limit: int = Query(default=20, ge=1, le=100, description="페이지당 리뷰 수 (최대 100)"),
    offset: int = Query(default=0, ge=0, description="건너뛸 리뷰 수 (페이지네이션)"),
    sentiment: str | None = Query(default=None, pattern="^(positive|negative|neutral)$", description="감성 필터 — positive / negative / neutral"),
) -> ReviewListResponse:
    async with get_tenant_session(tenant.id) as db:
        base_query = (
            select(Review, ReviewAnalytics)
            .outerjoin(ReviewAnalytics, Review.id == ReviewAnalytics.review_id)
        )
        if sentiment:
            base_query = base_query.where(ReviewAnalytics.sentiment == sentiment)

        total = await db.scalar(
            select(func.count()).select_from(base_query.subquery())
        )

        rows = await db.execute(
            base_query.order_by(Review.created_at.desc()).limit(limit).offset(offset)
        )

        items = []
        for review, analytics in rows.all():
            keywords: list[str] = []
            if analytics and analytics.keywords:
                try:
                    keywords = json.loads(analytics.keywords)
                except json.JSONDecodeError:
                    keywords = []

            items.append(ReviewItem(
                review_id=review.id,
                order_id=review.order_id,
                content=review.content,
                rating=review.rating,
                sentiment=analytics.sentiment if analytics else None,
                keywords=keywords,
                summary=analytics.summary if analytics else None,
            ))

    return ReviewListResponse(total=total or 0, items=items)


@router.get(
    "/search",
    response_model=list[SearchResultItem],
    summary="의미 기반 리뷰 검색",
    description=(
        "검색어를 OpenAI 임베딩으로 변환한 뒤 pgvector 코사인 유사도 기준으로 "
        "유사 리뷰를 반환합니다. sentiment / 평점 필터를 함께 적용할 수 있습니다. "
        "embedding이 생성되지 않은 리뷰는 결과에서 제외됩니다. "
        "JWT Bearer 토큰 인증 필요."
    ),
)
async def search_reviews(
    tenant: Annotated[TenantData, Depends(verify_jwt)],
    q: str = Query(description="검색어"),
    limit: int = Query(default=10, ge=1, le=50, description="반환 개수 (최대 50)"),
    sentiment: str | None = Query(
        default=None,
        pattern="^(positive|negative|neutral)$",
        description="감성 필터 — positive / negative / neutral",
    ),
    min_rating: float | None = Query(default=None, ge=1.0, le=5.0, description="최소 평점"),
    max_rating: float | None = Query(default=None, ge=1.0, le=5.0, description="최대 평점"),
) -> list[SearchResultItem]:
    vector = await _embed_query(q)
    vec_str = "[" + ",".join(str(x) for x in vector) + "]"

    conditions = ["r.embedding IS NOT NULL"]
    params: dict = {"vec": vec_str, "limit": limit}

    if sentiment:
        conditions.append("ra.sentiment = :sentiment")
        params["sentiment"] = sentiment
    if min_rating is not None:
        conditions.append("r.rating >= :min_rating")
        params["min_rating"] = min_rating
    if max_rating is not None:
        conditions.append("r.rating <= :max_rating")
        params["max_rating"] = max_rating

    where_clause = " AND ".join(conditions)
    # f-string은 조건절 키워드만 삽입 — 실제 값은 모두 bind param으로 처리
    sql = text(f"""
        SELECT r.id, r.content, r.rating, ra.sentiment,
               ROUND(CAST(1 - (r.embedding <=> CAST(:vec AS vector)) AS numeric), 3) AS similarity_score,
               r.created_at
        FROM reviews r
        LEFT JOIN review_analytics ra ON r.id = ra.review_id
        WHERE {where_clause}
        ORDER BY r.embedding <=> CAST(:vec AS vector)
        LIMIT :limit
    """)

    async with get_tenant_session(tenant.id) as db:
        rows = (await db.execute(sql, params)).all()

    return [
        SearchResultItem(
            review_id=row.id,
            content=row.content,
            rating=row.rating,
            sentiment=row.sentiment,
            similarity_score=float(row.similarity_score),
            created_at=row.created_at,
        )
        for row in rows
    ]
