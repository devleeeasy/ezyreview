# 리뷰 수집 엔드포인트 — 고객 리뷰 저장 후 AI 분석 태스크 발행
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.core.auth import TenantData, verify_jwt
from app.core.db import get_tenant_session
from app.models.tenant import Order, Review
from app.schemas.review import ReviewCreateRequest, ReviewCreateResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post(
    "",
    response_model=ReviewCreateResponse,
    status_code=201,
    summary="리뷰 등록",
    description="고객이 작성한 리뷰를 저장하고 AI 감성 분석 태스크를 즉시 발행합니다. JWT Bearer 토큰 인증 필요.",
)
async def create_review(
    body: ReviewCreateRequest,
    tenant: Annotated[TenantData, Depends(verify_jwt)],
) -> ReviewCreateResponse:
    async with get_tenant_session(tenant.id) as db:
        # 주문 존재 여부 확인
        result = await db.execute(
            select(Order).where(Order.order_id == body.order_id)
        )
        order = result.scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")

        # 동일 주문 리뷰 중복 방지
        existing = await db.execute(
            select(Review).where(Review.order_id == body.order_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="이미 리뷰가 등록된 주문입니다.")

        review = Review(
            order_id=body.order_id,
            content=body.content,
            rating=body.rating,
        )
        db.add(review)
        await db.commit()
        await db.refresh(review)

    # AI 분석 + 임베딩 태스크 즉시 발행 — 두 태스크는 독립적으로 실행되어 한쪽 실패가 다른 쪽에 영향을 주지 않는다
    from worker.tasks import analytics_task, generate_embedding_task
    analytics_task.delay(tenant.id, review.id)
    generate_embedding_task.delay(tenant.id, review.id)

    logger.info("Review created — tenant=%s order=%s review_id=%s", tenant.id, body.order_id, review.id)
    return ReviewCreateResponse(
        review_id=review.id,
        order_id=review.order_id,
        message="리뷰가 등록되었습니다. AI 분석이 시작됩니다.",
    )
