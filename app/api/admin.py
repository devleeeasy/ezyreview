# 관리용 엔드포인트 — 배치 작업 수동 트리거 (내부 운영용)
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import TenantData, verify_jwt
from worker.analytics import analyze_review, get_unanalyzed_review_ids

router = APIRouter(prefix="/admin", tags=["admin"])


class BatchResult(BaseModel):
    message: str
    tenant_id: int
    queued: int


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
