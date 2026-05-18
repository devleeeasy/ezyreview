# 인사이트 API — 리뷰 감성 요약 및 목록 조회
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.auth import TenantData, verify_jwt
from app.core.db import get_tenant_session
from app.models.tenant import Review, ReviewAnalytics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/insights", tags=["insights"])


class SentimentCount(BaseModel):
    positive: int = Field(description="긍정 리뷰 수")
    negative: int = Field(description="부정 리뷰 수")
    neutral: int = Field(description="중립 리뷰 수")
    unanalyzed: int = Field(description="아직 AI 분석이 완료되지 않은 리뷰 수")


class SummaryResponse(BaseModel):
    total_reviews: int = Field(description="전체 리뷰 수")
    avg_rating: float | None = Field(description="평균 평점 (리뷰 없으면 null)")
    sentiment: SentimentCount = Field(description="감성 분포")


class ReviewItem(BaseModel):
    review_id: int = Field(description="리뷰 고유 ID")
    order_id: str = Field(description="연결된 주문 ID")
    content: str | None = Field(description="리뷰 내용")
    rating: float | None = Field(description="평점 (1.0 ~ 5.0)")
    sentiment: str | None = Field(description="AI 감성 분석 결과 — positive / negative / neutral")
    keywords: list[str] = Field(description="AI가 추출한 핵심 키워드 목록")
    summary: str | None = Field(description="AI가 생성한 한 줄 요약")


class ReviewListResponse(BaseModel):
    total: int = Field(description="필터 조건에 해당하는 전체 리뷰 수")
    items: list[ReviewItem] = Field(description="현재 페이지 리뷰 목록")


@router.get(
    "/summary",
    response_model=SummaryResponse,
    summary="리뷰 인사이트 요약",
    description="전체 리뷰 수, 평균 평점, 감성 분포(긍정/부정/중립/미분석)를 반환합니다. JWT Bearer 토큰 인증 필요.",
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

    return SummaryResponse(
        total_reviews=total or 0,
        avg_rating=round(float(avg_rating), 2) if avg_rating else None,
        sentiment=SentimentCount(
            positive=sentiment_counts["positive"],
            negative=sentiment_counts["negative"],
            neutral=sentiment_counts["neutral"],
            unanalyzed=unanalyzed,
        ),
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
