# 관리용 엔드포인트 — 배치 작업 수동 트리거 (내부 운영용)
import json
import random
import time
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.auth import TenantData, verify_jwt
from app.core.db import get_tenant_session
from app.core.sample_reviews import PRODUCTS, sample_reviews
from app.models.tenant import Order, Review, ReviewAnalytics
from worker.analytics import analyze_review, get_unanalyzed_review_ids

router = APIRouter(prefix="/admin", tags=["admin"])


class BatchResult(BaseModel):
    """배치 분석 실행 결과."""

    message: str = Field(description="처리 결과 메시지")
    tenant_id: int = Field(description="분석을 실행한 테넌트 ID")
    queued: int = Field(description="이번 배치에서 분석 완료한 리뷰 수")


class ReportTriggerResult(BaseModel):
    """주간 리포트 수동 생성 결과."""

    message: str = Field(description="처리 결과 메시지")
    tenant_id: int = Field(description="리포트를 생성한 테넌트 ID")
    week_end: date = Field(description="리포트 기간 종료일 (이 날짜 포함 7일)")


class SeedTestDataResult(BaseModel):
    """테스트 데이터 생성 결과."""

    message: str = Field(description="처리 결과 메시지")
    tenant_id: int = Field(description="테스트 데이터를 생성한 테넌트 ID")
    review_ids: list[int] = Field(description="생성된 리뷰 ID 목록")


@router.post("/run-batch/{tenant_id}", response_model=BatchResult)
async def run_batch(
    tenant_id: int,
    tenant: Annotated[TenantData, Depends(verify_jwt)],
) -> BatchResult:
    # Celery 우회 — API 프로세스에서 직접 분석 실행
    review_ids = await get_unanalyzed_review_ids(tenant_id)
    for review_id in review_ids:
        await analyze_review(tenant_id, review_id)

    return BatchResult(message="batch completed", tenant_id=tenant_id, queued=len(review_ids))


@router.post("/generate-report/{tenant_id}", response_model=ReportTriggerResult)
async def generate_report(
    tenant_id: int,
    tenant: Annotated[TenantData, Depends(verify_jwt)],
    week_end: date | None = Query(
        default=None,
        description="리포트 기간 종료일 (YYYY-MM-DD). 생략하면 오늘 기준",
    ),
) -> ReportTriggerResult:
    import zoneinfo
    from datetime import datetime
    from worker.weekly_report import generate_weekly_report

    # week_end 미지정 시 오늘(KST)로 설정 — 오늘 생성된 리뷰까지 포함
    effective_week_end = week_end or datetime.now(zoneinfo.ZoneInfo("Asia/Seoul")).date()
    report_id = await generate_weekly_report(tenant_id, force_week_end=effective_week_end)

    if report_id is not None:
        from worker.tasks import send_weekly_report_email_task
        send_weekly_report_email_task.delay(tenant_id, report_id)

    return ReportTriggerResult(
        message="report generation completed",
        tenant_id=tenant_id,
        week_end=effective_week_end,
    )


@router.post("/seed-test-data/{tenant_id}", response_model=SeedTestDataResult)
async def seed_test_data(
    tenant_id: int,
    tenant: Annotated[TenantData, Depends(verify_jwt)],
    count: int = Query(
        default=10, ge=1, le=10,
        description="생성할 테스트 주문/리뷰 개수 (최대 10개)",
    ),
) -> SeedTestDataResult:
    """데모/시연용 테스트 리뷰 생성 (최대 10건).

    사전 계산된 임베딩·감성분석 결과를 사용하므로 OpenAI API가 호출되지 않습니다.
    """
    timestamp = int(time.time())
    review_ids: list[int] = []

    async with get_tenant_session(tenant_id) as db:
        for i, sample in enumerate(sample_reviews(count)):
            order_id = f"admin-seed-{timestamp}-{i + 1}"
            db.add(Order(
                order_id=order_id,
                customer_phone="010-0000-0000",
                product_name=random.choice(PRODUCTS),
                status="completed",
            ))
            review = Review(
                order_id=order_id,
                content=sample.content,
                rating=sample.rating,
                embedding=sample.embedding,
            )
            db.add(review)
            await db.flush()
            db.add(ReviewAnalytics(
                review_id=review.id,
                sentiment=sample.sentiment,
                keywords=json.dumps(sample.keywords, ensure_ascii=False),
                summary=sample.summary,
            ))
            review_ids.append(review.id)

        await db.commit()

    return SeedTestDataResult(
        message="test data created",
        tenant_id=tenant_id,
        review_ids=review_ids,
    )
