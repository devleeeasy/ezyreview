# Perplexica(Vane) 질의 자동 수집기 — Playwright + CDP로 SSE 스트리밍 원본 응답 캡처
#
# Vane의 채팅 응답은 네이티브 EventSource가 아니라 POST + fetch() 기반 스트림이라
# CDP의 Network.eventSourceMessageReceived는 발동하지 않는다(실측 확인).
# 대신 Network.responseReceived로 /api/chat 요청을 매칭하고, loadingFinished를
# 기다린 뒤 Network.getResponseBody로 전체 원본(NDJSON)을 가져오는 방식을 쓴다.
import asyncio
import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime

from playwright.async_api import CDPSession, Page, async_playwright
from sqlalchemy import select

from app.core.config import settings
from app.core.db import get_tenant_session
from app.models.citation import Query
from app.models.tenant import now_kst

logger = logging.getLogger(__name__)

TENANT_ID = 1  # Phase 1 — 단일 데모 테넌트 고정

# Vane 기본 채팅 모델은 구조화 출력(json_schema)을 지원하지 않아 400 에러가 남 —
# 매 세션마다 명시적으로 지원 모델을 선택해야 한다(실측 확인).
CHAT_MODEL_LABEL = "GPT 4.1 mini"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2
BACKOFF_MAX_SECONDS = 60
RESPONSE_TIMEOUT_SECONDS = 90


@dataclass
class CollectedAnswer:
    query_id: int
    query_text: str
    raw_response: str
    answer_text: str | None
    collected_at: datetime


async def _select_chat_model(page: Page) -> None:
    model_button = page.locator("button[aria-expanded]").nth(2)
    await model_button.click()
    await page.wait_for_timeout(300)
    await page.get_by_text(CHAT_MODEL_LABEL, exact=True).click()
    await page.wait_for_timeout(300)


def _reconstruct_answer(raw_response: str) -> str | None:
    """NDJSON 스트림에서 text 블록을 찾아, 그 블록의 마지막 /data replace 값(최종 누적 텍스트)을 반환."""
    text_block_id: str | None = None
    answer_text: str | None = None
    for line in raw_response.strip().split("\n"):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "block" and event.get("block", {}).get("type") == "text":
            text_block_id = event["block"]["id"]
        elif event.get("type") == "updateBlock" and event.get("blockId") == text_block_id:
            for patch in event.get("patch", []):
                if patch.get("path") == "/data":
                    answer_text = patch.get("value")
    return answer_text


async def _submit_and_capture(page: Page, cdp: CDPSession, query_text: str) -> str:
    """질의 제출 후 /api/chat 응답의 원본 SSE 바디(NDJSON)를 캡처해 반환."""
    target_request_id: str | None = None
    finished = asyncio.Event()

    def on_response(params: dict) -> None:
        nonlocal target_request_id
        if "/api/chat" in params.get("response", {}).get("url", ""):
            target_request_id = params.get("requestId")

    def on_finished(params: dict) -> None:
        if params.get("requestId") == target_request_id:
            finished.set()

    cdp.on("Network.responseReceived", on_response)
    cdp.on("Network.loadingFinished", on_finished)

    await page.locator("textarea").first.fill(query_text)
    await page.wait_for_timeout(300)
    await page.locator("button.bg-sky-500").click()

    await asyncio.wait_for(finished.wait(), timeout=RESPONSE_TIMEOUT_SECONDS)

    body = await cdp.send("Network.getResponseBody", {"requestId": target_request_id})
    return body.get("body", "")


async def collect_answer(query_id: int, query_text: str) -> CollectedAnswer:
    """질의 1건을 수집. 실패 시 점진적 재시도(최대 3회, 지수 백오프 최대 60초)."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                context = await browser.new_context(user_agent=random.choice(USER_AGENTS))
                page = await context.new_page()
                cdp = await context.new_cdp_session(page)
                await cdp.send("Network.enable")

                await page.goto(settings.VANE_URL, wait_until="networkidle", timeout=30000)
                await _select_chat_model(page)

                raw_response = await _submit_and_capture(page, cdp, query_text)
                return CollectedAnswer(
                    query_id=query_id,
                    query_text=query_text,
                    raw_response=raw_response,
                    answer_text=_reconstruct_answer(raw_response),
                    collected_at=now_kst(),
                )
            except Exception as exc:
                last_error = exc
                delay = min(BACKOFF_BASE_SECONDS ** attempt, BACKOFF_MAX_SECONDS)
                logger.warning(
                    "수집 실패 (attempt=%d/%d, query_id=%d): %s — %d초 후 재시도",
                    attempt, MAX_RETRIES, query_id, exc, delay,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(delay)
            finally:
                await browser.close()

    raise RuntimeError(f"수집 최종 실패 (query_id={query_id})") from last_error


async def collect_active_queries(tenant_id: int = TENANT_ID) -> list[CollectedAnswer]:
    """활성 질의 전체 순회 수집 — 요청 간 랜덤 딜레이 적용."""
    async with get_tenant_session(tenant_id) as db:
        result = await db.execute(select(Query).where(Query.active == True))  # noqa: E712
        queries = list(result.scalars().all())

    results: list[CollectedAnswer] = []
    for i, query in enumerate(queries):
        if i > 0:
            await asyncio.sleep(random.uniform(3, 8))
        try:
            answer = await collect_answer(query.id, query.text)
            results.append(answer)
            logger.info(
                "수집 완료 — query_id=%d, 답변 길이=%d자",
                query.id, len(answer.answer_text or ""),
            )
        except Exception:
            logger.exception("수집 실패 — query_id=%d", query.id)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(collect_active_queries())
