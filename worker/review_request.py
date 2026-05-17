# 리뷰 요청 알림 발송 — SendGrid (Railway 배포) / Gmail SMTP (로컬) 자동 선택
import logging
import smtplib
import zoneinfo
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from email.mime.text import MIMEText

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.db import _build_tenant_db_url
from app.models.tenant import Notification, Order

logger = logging.getLogger(__name__)

KST = zoneinfo.ZoneInfo("Asia/Seoul")

# -------------------------------------------------------------------
# 카카오 알림톡 연동 코드 (실서비스 전환 시 아래 주석 해제 후 사용)
# KAKAO_API_URL = "https://kakaoapi.aligo.in/akv10/alimtalk/send/"
#
# async def _send_kakao_alimtalk(phone: str, product_name: str) -> tuple[bool, str | None]:
#     if not settings.KAKAO_API_KEY:
#         logger.warning("KAKAO_API_KEY not set — skipping actual send (dev mode)")
#         return True, None
#     try:
#         async with httpx.AsyncClient(timeout=10.0) as client:
#             resp = await client.post(
#                 KAKAO_API_URL,
#                 data={
#                     "apikey": settings.KAKAO_API_KEY,
#                     "userid": "ezyreview",
#                     "receiver_1": phone,
#                     "subject_1": "리뷰 요청",
#                     "message_1": f"{product_name} 구매 감사합니다. 소중한 리뷰를 남겨주세요!",
#                 },
#             )
#             result = resp.json()
#             if result.get("code") == 0:
#                 return True, None
#             return False, result.get("message", "Unknown error")
#     except Exception as e:
#         logger.exception("Kakao API error: %s", e)
#         return False, str(e)
# -------------------------------------------------------------------


@asynccontextmanager
async def _worker_session(tenant_id: int) -> AsyncGenerator[AsyncSession, None]:
    # Celery 워커는 asyncio.run()으로 매 태스크마다 새 이벤트 루프를 생성한다.
    # NullPool을 쓰면 루프 간 커넥션 재사용 없이 매번 새로 연결하므로 충돌이 없다.
    engine = create_async_engine(_build_tenant_db_url(tenant_id), poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def send_review_request(tenant_id: int, order_id: str) -> None:
    async with _worker_session(tenant_id) as db:
        result = await db.execute(select(Order).where(Order.order_id == order_id))
        order = result.scalar_one_or_none()

        if not order:
            logger.warning("Order not found — tenant=%s order=%s", tenant_id, order_id)
            return

        notification = Notification(order_id=order_id, channel="email", status="pending")
        db.add(notification)
        await db.flush()

        success, error = await _send_notification(order.customer_phone, order.product_name)

        notification.status = "sent" if success else "failed"
        notification.sent_at = datetime.now(KST) if success else None
        notification.error_message = error
        await db.commit()

        logger.info("Notification %s — tenant=%s order=%s", notification.status, tenant_id, order_id)


async def _send_notification(recipient: str, product_name: str) -> tuple[bool, str | None]:
    # SendGrid 설정 시 우선 사용 (Railway 등 클라우드 환경 — SMTP 포트 차단 우회)
    if settings.SENDGRID_API_KEY and settings.SENDGRID_FROM_EMAIL:
        return _send_via_sendgrid(recipient, product_name)

    # Gmail SMTP 폴백 (로컬 개발 환경)
    if settings.GMAIL_USER and settings.GMAIL_APP_PASSWORD:
        return await _send_via_gmail(recipient, product_name)

    logger.warning("No email provider configured — skipping send (dev mode)")
    return True, None


def _send_via_sendgrid(recipient: str, product_name: str) -> tuple[bool, str | None]:
    subject = f"[ezyreview] {product_name} 구매 후기를 남겨주세요 🙏"
    body = f"""안녕하세요!

{product_name}을(를) 구매해 주셔서 감사합니다.

소중한 리뷰를 남겨주시면 더 나은 서비스를 제공하는 데 큰 도움이 됩니다.

감사합니다.
ezyreview 팀 드림
"""
    try:
        message = Mail(
            from_email=settings.SENDGRID_FROM_EMAIL,
            to_emails=recipient,
            subject=subject,
            plain_text_content=body,
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


async def _send_via_gmail(recipient: str, product_name: str) -> tuple[bool, str | None]:
    subject = f"[ezyreview] {product_name} 구매 후기를 남겨주세요 🙏"
    body = f"""안녕하세요!

{product_name}을(를) 구매해 주셔서 감사합니다.

소중한 리뷰를 남겨주시면 더 나은 서비스를 제공하는 데 큰 도움이 됩니다.

감사합니다.
ezyreview 팀 드림
"""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.GMAIL_USER
    msg["To"] = recipient

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(settings.GMAIL_USER, settings.GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        logger.info("Gmail sent — to=%s", recipient)
        return True, None
    except Exception as e:
        logger.exception("Gmail SMTP error: %s", e)
        return False, str(e)
