# Celery 태스크 — 알림 발송 및 AI 분석 비동기 처리
import asyncio
import logging

from worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
)
def review_request_task(self, tenant_id: int, order_id: str) -> None:
    from worker.review_request import send_review_request
    asyncio.run(send_review_request(tenant_id, order_id))


@celery_app.task(bind=True, max_retries=3)
def analytics_task(self, tenant_id: int, review_id: int) -> None:
    # 3주차 구현 예정
    logger.info("analytics_task queued — tenant_id=%s review_id=%s", tenant_id, review_id)
