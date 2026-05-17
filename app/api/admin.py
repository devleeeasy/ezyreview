# 관리용 엔드포인트 — 배치 작업 수동 트리거 (내부 운영용)
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter(prefix="/admin", tags=["admin"])


class BatchResult(BaseModel):
    message: str
    tenant_id: int


@router.post("/run-batch/{tenant_id}", response_model=BatchResult)
async def run_batch(
    tenant_id: int,
    x_admin_key: str | None = Header(default=None),
) -> BatchResult:
    if x_admin_key != settings.JWT_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    from worker.tasks import batch_analytics_task
    batch_analytics_task.delay(tenant_id)

    return BatchResult(message="batch dispatched", tenant_id=tenant_id)
