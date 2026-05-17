# 웹훅 수신 엔드포인트 — 테넌트 인증 → 중복 차단 → 태스크 발행
import json
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_by_api_key
from app.core.config import settings
from app.core.db import create_tenant_db, get_main_db, get_tenant_session
from app.models.main import WebhookLog
from app.models.tenant import Order
from app.schemas.webhook import WebhookRequest, WebhookResponse

logger = logging.getLogger(__name__)
router = APIRouter()

_redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

DEDUP_TTL = 86400  # 24시간


@router.post("/webhook/{api_key}", response_model=WebhookResponse)
async def receive_webhook(
    api_key: str,
    body: WebhookRequest,
    db: AsyncSession = Depends(get_main_db),
) -> WebhookResponse:
    # 1) 테넌트 인증
    tenant = await get_tenant_by_api_key(api_key, db)
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # 2) 중복 수신 차단 — 동일 order_id 재전송 시 즉시 반환
    dedup_key = f"webhook:dedup:{tenant.id}:{body.order_id}"
    is_new = await _redis.set(dedup_key, "1", ex=DEDUP_TTL, nx=True)
    if not is_new:
        logger.info("Duplicate webhook — tenant=%s order=%s", tenant.id, body.order_id)
        return WebhookResponse(status="duplicated")

    # 3) WebhookLog 기록
    log = WebhookLog(
        tenant_id=tenant.id,
        order_id=body.order_id,
        payload=json.dumps(body.model_dump(), ensure_ascii=False),
        status="received",
    )
    db.add(log)
    await db.commit()

    # 4) 테넌트 DB 초기화 (최초 수신 시 자동 생성, 이후 Redis 캐시로 스킵)
    db_ready_key = f"tenant:db_ready:{tenant.id}"
    if not await _redis.exists(db_ready_key):
        await create_tenant_db(tenant.id)
        await _redis.set(db_ready_key, "1")

    # 5) tenant_db에 Order 저장
    async with get_tenant_session(tenant.id) as tenant_db:
        order = Order(
            order_id=body.order_id,
            customer_phone=body.customer_phone,
            product_name=body.product_name,
        )
        tenant_db.add(order)
        await tenant_db.commit()

    # 6) Celery 태스크 발행 — 응답 후 비동기 처리
    from worker.tasks import review_request_task
    review_request_task.delay(tenant.id, body.order_id)

    logger.info("Webhook accepted — tenant=%s order=%s", tenant.id, body.order_id)
    return WebhookResponse(status="accepted")
