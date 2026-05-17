# 인사이트 API — 리뷰 감성 요약 및 목록 조회
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.auth import TenantData, verify_api_key
from app.core.db import get_tenant_session
from app.models.tenant import Review, ReviewAnalytics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/insights", tags=["insights"])


class SentimentCount(BaseModel):
    positive: int
    negative: int
    neutral: int
    unanalyzed: int


class SummaryResponse(BaseModel):
    total_reviews: int
    avg_rating: float | None
    sentiment: SentimentCount


class ReviewItem(BaseModel):
    review_id: int
    order_id: str
    content: str | None
    rating: float | None
    sentiment: str | None
    keywords: list[str]
    summary: str | None


class ReviewListResponse(BaseModel):
    total: int
    items: list[ReviewItem]


@router.get("/summary", response_model=SummaryResponse)
async def get_summary(
    tenant: Annotated[TenantData, Depends(verify_api_key)],
) -> SummaryResponse:
    async with get_tenant_session(tenant.id) as db:
        total = await db.scalar(select(func.count()).select_from(Review))
        avg_rating = await db.scalar(select(func.avg(Review.rating)))

        sentiment_counts: dict[str, int] = {}
        for s in ("positive", "negative", "neutral"):
            count = await db.scalar(
                select(func.count())
                .select_from(ReviewAnalytics)
                .where(ReviewAnalytics.sentiment == s)
            )
            sentiment_counts[s] = count or 0

        analyzed_count = await db.scalar(
            select(func.count()).select_from(ReviewAnalytics)
        )
        unanalyzed = max((total or 0) - (analyzed_count or 0), 0)

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


@router.get("/reviews", response_model=ReviewListResponse)
async def get_reviews(
    tenant: Annotated[TenantData, Depends(verify_api_key)],
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sentiment: str | None = Query(default=None, pattern="^(positive|negative|neutral)$"),
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
