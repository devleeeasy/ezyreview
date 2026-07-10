# 관리용 엔드포인트 — 배치 작업 수동 트리거 (내부 운영용)
import json
import random
import time
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.auth import TenantData, verify_jwt
from app.core.db import create_tenant_db, get_tenant_session
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
    """웹훅으로 수신된 미분석 리뷰를 즉시 AI 분석합니다.

    Celery 큐를 우회해 API 프로세스에서 동기 실행합니다.
    worker가 다운됐거나 분석이 밀렸을 때 수동으로 따라잡기 위한 운영용 엔드포인트입니다.

    **주의:** seed-test-data로 생성한 데이터는 분석 결과까지 포함하므로 이 엔드포인트로 처리할 대상이 없습니다.
    실제 웹훅으로 리뷰가 쌓인 경우에만 의미 있습니다.
    """
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
    """7일치 리뷰를 집계해 주간 리포트를 즉시 생성하고 이메일로 발송합니다.

    매주 월요일 오전 9시 자동 실행되는 스케줄을 수동으로 트리거하는 엔드포인트입니다.
    week_end를 지정하면 해당 날짜 기준 7일 이내 리뷰를 집계합니다.

    **선행 조건:** 대상 기간에 분석 완료된 리뷰(ReviewAnalytics)가 있어야 합니다.
    seed-test-data로 생성한 데이터라면 분석 결과가 포함되어 있으므로 바로 사용 가능합니다.
    """
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
    """데모·시연용 샘플 주문/리뷰/분석 결과를 한번에 생성합니다 (최대 10건).

    사전 계산된 임베딩·감성분석 결과를 사용하므로 OpenAI API가 호출되지 않습니다.
    Order → Review → ReviewAnalytics 세트를 즉시 INSERT하므로,
    이후 insights API(검색·리포트)를 바로 시연할 수 있습니다.

    **run-batch와의 차이:** 이 엔드포인트는 데이터가 없는 상태에서 시작할 때 사용합니다.
    run-batch는 웹훅으로 실제 리뷰가 쌓인 뒤 AI 분석이 밀렸을 때 사용합니다.
    """
    timestamp = int(time.time())
    review_ids: list[int] = []

    # 웹훅을 한 번도 받지 않은 신규 테넌트는 tenant DB가 아직 없으므로 먼저 생성
    await create_tenant_db(tenant_id)

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
