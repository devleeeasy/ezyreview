# 관리용 엔드포인트 — 배치 작업 수동 트리거 (내부 운영용)
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.auth import TenantData, verify_jwt
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
