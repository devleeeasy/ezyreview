# Celery 앱 설정 — Redis 브로커, Asia/Seoul 타임존, beat 스케줄
from celery import Celery
from celery.schedules import crontab

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
    beat_schedule={
        # 매일 새벽 2시(KST) 미분석 리뷰 일괄 분석
        "nightly-analytics": {
            "task": "worker.tasks.nightly_analytics_task",
            "schedule": crontab(hour=2, minute=0),
        },
        # 매주 월요일 오전 9시(KST) 전 테넌트 주간 리포트 생성
        "weekly-report": {
            "task": "worker.tasks.weekly_report_all_tenants_task",
            "schedule": crontab(hour=9, minute=0, day_of_week=1),
        },
    },
)
