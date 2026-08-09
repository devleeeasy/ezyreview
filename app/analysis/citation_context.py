# 인용 문맥 분석 — 언급 문맥 감성 분류 + 질의 임베딩 (기존 OpenAI 연동 방식 재사용)
import json
import logging

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

SENTIMENT_SYSTEM_PROMPT = """당신은 AI 검색엔진 답변 속 브랜드 언급 문맥을 분석하는 AI입니다.
주어진 문맥에서 해당 브랜드가 어떤 뉘앙스로 언급되었는지 분류하세요.
반드시 아래 JSON 형식으로만 응답하세요.

{
  "sentiment": "positive" | "negative" | "neutral"
}"""


async def classify_context_sentiment(context_snippet: str) -> str:
    """인용 문맥 텍스트의 긍정/중립/부정을 분류한다."""
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — returning dummy sentiment (dev mode)")
        return "neutral"

    # Celery 워커에서도 호출되므로 이벤트 루프 불일치를 피하기 위해 호출 시점에 클라이언트를 생성한다.
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SENTIMENT_SYSTEM_PROMPT},
                {"role": "user", "content": f"문맥: {context_snippet}"},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        return data.get("sentiment", "neutral")
    except Exception as e:
        logger.exception("OpenAI sentiment classification error: %s", e)
        raise


async def embed_query_text(text: str) -> list[float]:
    """질의 텍스트를 벡터로 변환한다 (유사 질의 클러스터링용)."""
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — returning zero vector (dev mode)")
        return [0.0] * 1536

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    try:
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return response.data[0].embedding
    except Exception as e:
        logger.exception("OpenAI embedding API error: %s", e)
        raise
