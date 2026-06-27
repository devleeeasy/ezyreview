# 주간 리뷰 요약 리포트 생성 — 7일치 리뷰 집계 후 OpenAI 요약 → WeeklyReport 저장
import json
import logging
import zoneinfo
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

from openai import AsyncOpenAI
from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.db import _build_tenant_db_url
from app.models.tenant import Review, ReviewAnalytics, WeeklyReport

logger = logging.getLogger(__name__)

KST = zoneinfo.ZoneInfo("Asia/Seoul")

WEEKLY_REPORT_PROMPT = """당신은 이커머스 리뷰 분석 AI입니다.
주어진 리뷰 목록을 분석하여 반드시 아래 JSON 형식으로만 응답하세요.

{
  "summary": "이번 주 전반적인 요약 2-3문장",
  "top_issues": ["불만1", "불만2", "불만3"],
  "top_positives": ["긍정1", "긍정2", "긍정3"]
}

top_issues와 top_positives는 정확히 3개씩 반환하세요.
해당 유형의 리뷰가 적으면 빈 문자열("")로 채우세요."""


@asynccontextmanager
async def _worker_session(tenant_id: int) -> AsyncGenerator[AsyncSession, None]:
    # NullPool: Celery는 asyncio.run()으로 매 태스크마다 새 이벤트 루프를 생성하므로
    # 루프 간 커넥션 재사용 없이 매번 새로 연결해야 충돌이 없다.
    engine = create_async_engine(_build_tenant_db_url(tenant_id), poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def generate_weekly_report(tenant_id: int, force_week_end: date | None = None) -> int | None:
    now = datetime.now(KST)
    # force_week_end 지정 시 해당 날짜 기준, 기본은 어제까지(전주 완성분)
    week_end: date = force_week_end if force_week_end is not None else now.date() - timedelta(days=1)
    week_start: date = week_end - timedelta(days=6)

    async with _worker_session(tenant_id) as db:
        existing = await db.execute(
            select(WeeklyReport).where(
                WeeklyReport.tenant_id == tenant_id,
                WeeklyReport.week_start == week_start,
            )
        )
        if existing.scalar_one_or_none():
            logger.info("Weekly report already exists — tenant=%s week=%s", tenant_id, week_start)
            return None

        week_start_dt = datetime(week_start.year, week_start.month, week_start.day, 0, 0, 0, tzinfo=KST)
        week_end_dt = datetime(week_end.year, week_end.month, week_end.day, 23, 59, 59, tzinfo=KST)

        stats = await db.execute(
            select(func.count(Review.id), func.avg(Review.rating)).where(
                Review.created_at >= week_start_dt,
                Review.created_at <= week_end_dt,
            )
        )
        total_reviews, avg_rating = stats.one()

        if not total_reviews:
            # 리뷰가 없어도 리포트를 저장하고 이메일 발송 — 아무 반응 없으면 오류처럼 보임
            logger.info("No reviews this week — tenant=%s week=%s (sending empty report)", tenant_id, week_start)
            report = WeeklyReport(
                tenant_id=tenant_id,
                week_start=week_start,
                week_end=week_end,
                total_reviews=0,
                avg_rating=None,
                summary="이번 주 수집된 리뷰가 없습니다.",
                top_issues=json.dumps([], ensure_ascii=False),
                top_positives=json.dumps([], ensure_ascii=False),
            )
            db.add(report)
            await db.commit()
            logger.info("Empty weekly report saved — tenant=%s week=%s", tenant_id, week_start)
            return report.id

        # 감성 레이블 포함 리뷰 본문 최대 50건
        rows = (
            await db.execute(
                select(Review.content, ReviewAnalytics.sentiment)
                .outerjoin(ReviewAnalytics, Review.id == ReviewAnalytics.review_id)
                .where(
                    Review.created_at >= week_start_dt,
                    Review.created_at <= week_end_dt,
                    Review.content.isnot(None),
                )
                .limit(50)
            )
        ).all()

        summary, top_issues, top_positives = await _call_openai_for_report(rows)

        report = WeeklyReport(
            tenant_id=tenant_id,
            week_start=week_start,
            week_end=week_end,
            total_reviews=total_reviews,
            avg_rating=round(float(avg_rating), 2) if avg_rating else None,
            summary=summary,
            top_issues=json.dumps(top_issues, ensure_ascii=False),
            top_positives=json.dumps(top_positives, ensure_ascii=False),
        )
        db.add(report)
        await db.commit()
        report_id = report.id

    logger.info(
        "Weekly report saved — tenant=%s week=%s total_reviews=%d",
        tenant_id, week_start, total_reviews,
    )
    return report_id


async def _call_openai_for_report(
    review_rows: list[Row],
) -> tuple[str, list[str], list[str]]:
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — returning dummy weekly report (dev mode)")
        return (
            "개발 환경 더미 주간 요약입니다.",
            ["더미 불만1", "더미 불만2", "더미 불만3"],
            ["더미 긍정1", "더미 긍정2", "더미 긍정3"],
        )

    reviews_text = "\n".join(
        f"[{row.sentiment or '미분석'}] {row.content}" for row in review_rows
    )

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": WEEKLY_REPORT_PROMPT},
                {"role": "user", "content": f"리뷰 목록:\n{reviews_text}"},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        return (
            data.get("summary", ""),
            data.get("top_issues", []),
            data.get("top_positives", []),
        )
    except Exception as e:
        logger.exception("OpenAI API error in weekly report: %s", e)
        raise
