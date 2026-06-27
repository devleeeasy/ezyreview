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


@celery_app.task(
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
)
def analytics_task(self, tenant_id: int, review_id: int) -> None:
    from worker.analytics import analyze_review
    asyncio.run(analyze_review(tenant_id, review_id))


@celery_app.task(
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
)
def generate_embedding_task(self, tenant_id: int, review_id: int) -> None:
    from worker.embedding import generate_embedding
    asyncio.run(generate_embedding(tenant_id, review_id))


@celery_app.task(bind=True, max_retries=1)
def batch_analytics_task(self, tenant_id: int) -> None:
    """미분석 리뷰를 일괄 analytics_task + generate_embedding_task로 위임한다."""
    import asyncpg
    from sqlalchemy.exc import OperationalError
    from worker.analytics import get_unanalyzed_review_ids

    try:
        review_ids = asyncio.run(get_unanalyzed_review_ids(tenant_id))
    except (OperationalError, asyncpg.InvalidCatalogNameError) as e:
        # 웹훅을 아직 수신하지 않아 테넌트 DB가 없는 경우 스킵
        is_missing_db = isinstance(e, asyncpg.InvalidCatalogNameError) or \
                        isinstance(getattr(e, "orig", None), asyncpg.InvalidCatalogNameError)
        if is_missing_db:
            logger.warning("batch_analytics_task skipped — tenant=%s (DB not ready)", tenant_id)
            return
        raise

    for review_id in review_ids:
        analytics_task.delay(tenant_id, review_id)
        generate_embedding_task.delay(tenant_id, review_id)
    logger.info("batch_analytics_task — tenant=%s queued=%d", tenant_id, len(review_ids))


@celery_app.task(
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
)
def generate_weekly_report_task(self, tenant_id: int) -> None:
    import asyncpg
    from sqlalchemy.exc import OperationalError
    from worker.weekly_report import generate_weekly_report

    try:
        report_id = asyncio.run(generate_weekly_report(tenant_id))
    except (OperationalError, asyncpg.InvalidCatalogNameError) as e:
        # 웹훅을 아직 수신하지 않아 테넌트 DB가 없는 경우 스킵
        is_missing_db = isinstance(e, asyncpg.InvalidCatalogNameError) or \
                        isinstance(getattr(e, "orig", None), asyncpg.InvalidCatalogNameError)
        if is_missing_db:
            logger.warning("generate_weekly_report_task skipped — tenant=%s (DB not ready)", tenant_id)
            return
        raise

    if report_id is not None:
        send_weekly_report_email_task.delay(tenant_id, report_id)


@celery_app.task(
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
)
def send_weekly_report_email_task(self, tenant_id: int, report_id: int) -> None:
    from worker.weekly_report_email import send_weekly_report_email
    asyncio.run(send_weekly_report_email(tenant_id, report_id))


@celery_app.task(bind=True, max_retries=1)
def weekly_report_all_tenants_task(self) -> None:
    """매주 월요일 오전 9시 — 모든 활성 테넌트의 주간 리포트를 병렬 생성."""
    from celery import group
    from sqlalchemy import select as _select
    from app.core.db import _main_session_factory
    from app.models.main import Tenant

    async def _get_tenant_ids() -> list[int]:
        async with _main_session_factory() as db:
            result = await db.execute(
                _select(Tenant.id).where(Tenant.is_active == True)
            )
            return list(result.scalars().all())

    tenant_ids = asyncio.run(_get_tenant_ids())
    job = group(generate_weekly_report_task.s(tid) for tid in tenant_ids)
    job.apply_async()
    logger.info("weekly_report_all_tenants_task — dispatched for %d tenants", len(tenant_ids))


@celery_app.task(bind=True, max_retries=1)
def nightly_analytics_task(self) -> None:
    """매일 새벽 2시 — 모든 활성 테넌트의 미분석 리뷰 일괄 분석 실행."""
    import asyncio as _asyncio
    from sqlalchemy import select as _select
    from app.core.db import _main_session_factory
    from app.models.main import Tenant

    async def _get_tenant_ids() -> list[int]:
        async with _main_session_factory() as db:
            result = await db.execute(
                _select(Tenant.id).where(Tenant.is_active == True)
            )
            return list(result.scalars().all())

    tenant_ids = _asyncio.run(_get_tenant_ids())
    for tenant_id in tenant_ids:
        batch_analytics_task.delay(tenant_id)
    logger.info("nightly_analytics_task — dispatched for %d tenants", len(tenant_ids))
