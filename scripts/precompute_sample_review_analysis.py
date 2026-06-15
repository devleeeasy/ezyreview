# sample_reviews.py 풀 전체에 대해 임베딩+감성분석을 1회 계산해 JSON으로 저장 (1회성 스크립트)
# admin/seed-test-data가 매 호출마다 OpenAI를 호출하지 않도록 결과를 캐싱한다.
import asyncio
import json

from openai import AsyncOpenAI

from app.core.config import settings
from app.core.sample_reviews import NEGATIVE_REVIEWS, NEUTRAL_REVIEWS, POSITIVE_REVIEWS
from worker.analytics import _call_openai

OUTPUT_PATH = "app/core/sample_reviews_analysis.json"


async def fetch_embeddings(contents: list[str]) -> list[list[float]]:
    """전체 텍스트를 OpenAI batch call 한 번으로 임베딩 생성."""
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    response = await client.embeddings.create(model="text-embedding-3-small", input=contents)
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


async def analyze_all(contents: list[str]) -> list[tuple[str, list[str], str]]:
    """텍스트별 감성분석을 동시 5개씩 처리."""
    semaphore = asyncio.Semaphore(5)

    async def analyze_one(content: str) -> tuple[str, list[str], str]:
        async with semaphore:
            return await _call_openai(content)

    return await asyncio.gather(*(analyze_one(content) for content in contents))


async def main() -> None:
    contents = POSITIVE_REVIEWS + NEGATIVE_REVIEWS + NEUTRAL_REVIEWS

    print(f"임베딩 계산 중... ({len(contents)}개)")
    embeddings = await fetch_embeddings(contents)

    print(f"감성분석 계산 중... ({len(contents)}개)")
    analyses = await analyze_all(contents)

    result = {
        content: {
            "embedding": [round(value, 6) for value in embedding],
            "sentiment": sentiment,
            "keywords": keywords,
            "summary": summary,
        }
        for content, embedding, (sentiment, keywords, summary) in zip(contents, embeddings, analyses)
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)

    print(f"완료 - {OUTPUT_PATH}에 {len(result)}개 항목 저장")


if __name__ == "__main__":
    asyncio.run(main())
