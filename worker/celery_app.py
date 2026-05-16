# Celery 앱 설정 — Redis 브로커, Asia/Seoul 타임존
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "ezyreview",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["worker.tasks"],
)

celery_app.conf.update(
    timezone="Asia/Seoul",
    enable_utc=False,
)
