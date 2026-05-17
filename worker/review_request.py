# 리뷰 요청 알림 발송 — Gmail SMTP (카카오 알림톡 → Gmail로 전환)
import logging
import smtplib
import zoneinfo
from datetime import datetime
from email.mime.text import MIMEText

from sqlalchemy import select

from app.core.config import settings
from app.core.db import get_tenant_session
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


async def send_review_request(tenant_id: int, order_id: str) -> None:
    async with get_tenant_session(tenant_id) as db:
        result = await db.execute(select(Order).where(Order.order_id == order_id))
        order = result.scalar_one_or_none()

        if not order:
            logger.warning("Order not found — tenant=%s order=%s", tenant_id, order_id)
            return

        notification = Notification(order_id=order_id, channel="email", status="pending")
        db.add(notification)
        await db.flush()

        success, error = await _send_email(order.customer_phone, order.product_name)

        notification.status = "sent" if success else "failed"
        notification.sent_at = datetime.now(KST) if success else None
        notification.error_message = error
        await db.commit()

        logger.info("Notification %s — tenant=%s order=%s", notification.status, tenant_id, order_id)


async def _send_email(recipient: str, product_name: str) -> tuple[bool, str | None]:
    # Gmail 미설정 시 개발 환경으로 간주하고 성공 처리
    if not settings.GMAIL_USER or not settings.GMAIL_APP_PASSWORD:
        logger.warning("GMAIL_USER/GMAIL_APP_PASSWORD not set — skipping actual send (dev mode)")
        return True, None

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
    msg["To"] = recipient  # 실서비스에서는 고객 이메일 주소로 변경

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(settings.GMAIL_USER, settings.GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        return True, None
    except Exception as e:
        logger.exception("Gmail SMTP error: %s", e)
        return False, str(e)
