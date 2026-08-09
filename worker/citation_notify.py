# 인용 수집 결과 Google Chat Webhook 알림 — SendGrid/카카오 알림 발송과 동일하게
# 설정 미존재 시 스킵 + try/except로 알림 실패가 수집 파이프라인에 전파되지 않도록 한다.
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def notify_google_chat(text: str) -> bool:
    if not settings.GOOGLE_CHAT_WEBHOOK_URL:
        logger.info("GOOGLE_CHAT_WEBHOOK_URL not set — skipping notification")
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                settings.GOOGLE_CHAT_WEBHOOK_URL, json={"text": text}
            )
            response.raise_for_status()
        logger.info("Google Chat notification sent")
        return True
    except Exception as e:
        logger.exception("Google Chat notification failed: %s", e)
        return False
