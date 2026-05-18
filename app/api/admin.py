# 관리용 엔드포인트 — 배치 작업 수동 트리거 (내부 운영용)
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.auth import TenantData, verify_jwt
from worker.analytics import analyze_review, get_unanalyzed_review_ids

router = APIRouter(prefix="/admin", tags=["admin"])


class BatchResult(BaseModel):
    """배치 분석 실행 결과."""

    message: str = Field(description="처리 결과 메시지")
    tenant_id: int = Field(description="분석을 실행한 테넌트 ID")
    queued: int = Field(description="이번 배치에서 분석 완료한 리뷰 수")


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
