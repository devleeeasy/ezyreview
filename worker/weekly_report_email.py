# 주간 리뷰 요약 리포트 이메일 발송 — SendGrid (클라우드) / Gmail SMTP (로컬) 자동 선택
import json
import logging
import smtplib
import zoneinfo
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.db import _build_tenant_db_url
from app.models.tenant import WeeklyReport

logger = logging.getLogger(__name__)

KST = zoneinfo.ZoneInfo("Asia/Seoul")


@asynccontextmanager
async def _tenant_worker_session(tenant_id: int) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(_build_tenant_db_url(tenant_id), poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@asynccontextmanager
async def _main_worker_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def send_weekly_report_email(tenant_id: int, report_id: int) -> None:
    async with _tenant_worker_session(tenant_id) as db:
        result = await db.execute(
            select(WeeklyReport).where(WeeklyReport.id == report_id)
        )
        report = result.scalar_one_or_none()

    if not report:
        logger.warning("Weekly report not found — tenant=%s report_id=%s", tenant_id, report_id)
        return

    from app.models.main import Tenant
    async with _main_worker_session() as db:
        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()

    if not tenant or not tenant.email:
        logger.info("Tenant email not set — skipping report email (tenant=%s)", tenant_id)
        return

    subject = (
        f"[EzyReview] {tenant.name} 주간 리뷰 리포트"
        f" ({report.week_start} ~ {report.week_end})"
    )
    html_body = _build_html_body(report, tenant.name)

    if settings.SENDGRID_API_KEY and settings.SENDGRID_FROM_EMAIL:
        success, error = _send_via_sendgrid(tenant.email, subject, html_body)
    elif settings.GMAIL_USER and settings.GMAIL_APP_PASSWORD:
        success, error = await _send_via_gmail(tenant.email, subject, html_body)
    else:
        logger.warning("No email provider configured — skipping weekly report email (tenant=%s)", tenant_id)
        return

    if not success:
        # 이메일 실패는 리포트와 독립적으로 처리 — 예외 미전파, 로그만 남김
        logger.error(
            "Weekly report email failed — tenant=%s report_id=%s error=%s",
            tenant_id, report_id, error,
        )
        return

    async with _tenant_worker_session(tenant_id) as db:
        result = await db.execute(
            select(WeeklyReport).where(WeeklyReport.id == report_id)
        )
        row = result.scalar_one_or_none()
        if row:
            row.mail_sent_at = datetime.now(KST)
            await db.commit()

    logger.info(
        "Weekly report email sent — tenant=%s report_id=%s to=%s",
        tenant_id, report_id, tenant.email,
    )


def _build_html_body(report: WeeklyReport, tenant_name: str) -> str:
    def _parse_list(value: str | None) -> list[str]:
        if not value:
            return []
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []

    issues = _parse_list(report.top_issues)
    positives = _parse_list(report.top_positives)
    avg_str = f"{report.avg_rating:.1f}" if report.avg_rating else "N/A"

    issues_html = "".join(f"<li>{item}</li>" for item in issues if item) or "<li>해당 없음</li>"
    positives_html = "".join(f"<li>{item}</li>" for item in positives if item) or "<li>해당 없음</li>"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#333;">
  <h2 style="border-bottom:2px solid #4CAF50;padding-bottom:10px;">[EzyReview] 주간 리뷰 리포트</h2>
  <p style="color:#888;margin-top:4px;">{report.week_start} ~ {report.week_end} | {tenant_name}</p>

  <div style="background:#f9f9f9;border-radius:8px;padding:16px;margin:20px 0;">
    <h3 style="margin-top:0;">이번 주 요약</h3>
    <p>총 리뷰 수: <strong>{report.total_reviews}건</strong>&nbsp;&nbsp;|&nbsp;&nbsp;평균 평점: <strong>{avg_str}점</strong></p>
    <p style="margin-bottom:0;">{report.summary or ""}</p>
  </div>

  <div style="margin:20px 0;">
    <h3>주요 불만</h3>
    <ul style="padding-left:20px;line-height:1.8;">{issues_html}</ul>
  </div>

  <div style="margin:20px 0;">
    <h3>주요 긍정</h3>
    <ul style="padding-left:20px;line-height:1.8;">{positives_html}</ul>
  </div>

  <hr style="border:none;border-top:1px solid #eee;margin-top:30px;">
  <p style="color:#aaa;font-size:12px;text-align:center;">이 메일은 EzyReview에서 자동 발송되었습니다.</p>
</body>
</html>"""


def _send_via_sendgrid(recipient: str, subject: str, html_body: str) -> tuple[bool, str | None]:
    try:
        message = Mail(
            from_email=settings.SENDGRID_FROM_EMAIL,
            to_emails=recipient,
            subject=subject,
            html_content=html_body,
        )
        sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
        response = sg.send(message)
        if response.status_code in (200, 202):
            logger.info("SendGrid sent — to=%s status=%s", recipient, response.status_code)
            return True, None
        return False, f"SendGrid status: {response.status_code}"
    except Exception as e:
        logger.exception("SendGrid error: %s", e)
        return False, str(e)


async def _send_via_gmail(recipient: str, subject: str, html_body: str) -> tuple[bool, str | None]:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.GMAIL_USER
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(settings.GMAIL_USER, settings.GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        logger.info("Gmail sent — to=%s", recipient)
        return True, None
    except Exception as e:
        logger.exception("Gmail SMTP error: %s", e)
        return False, str(e)
