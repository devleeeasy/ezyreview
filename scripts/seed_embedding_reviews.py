# tenant_1_db에 임베딩 포함 리뷰 100개 삽입 — OpenAI batch call로 한 번에 처리
import asyncio
import random
import zoneinfo
from datetime import datetime

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.db import _build_tenant_db_url
from app.models.tenant import Review

KST = zoneinfo.ZoneInfo("Asia/Seoul")
TENANT_ID = 1

POSITIVE_REVIEWS = [
    "배송이 정말 빠르고 포장도 꼼꼼해서 완벽했어요. 제품 품질도 기대 이상입니다!",
    "가격 대비 품질이 너무 좋아요. 다음에도 꼭 다시 구매할 예정입니다.",
    "디자인이 예쁘고 실용적이에요. 주변 친구들에게도 추천했습니다.",
    "고객서비스가 친절하고 빠른 답변 덕분에 문제가 금방 해결됐어요.",
    "소재가 고급스럽고 마감이 깔끔합니다. 오래 쓸 수 있을 것 같아요.",
    "색상이 사진과 똑같이 나왔어요. 설명 그대로라 신뢰가 갑니다.",
    "내구성이 뛰어나서 오래 써도 변형이 없네요. 만족스러워요.",
    "기능이 다양하고 사용법이 직관적이라 쉽게 쓸 수 있었어요.",
    "성능이 뛰어나고 배터리도 오래가서 매우 만족합니다.",
    "음질이 너무 좋아서 매일 사용하고 있어요. 강력 추천합니다!",
    "착용감이 편안하고 사이즈가 딱 맞았어요. 재구매 의사 100%입니다.",
    "가성비 최고! 이 가격에 이 품질이라니 정말 놀랍습니다.",
    "빠른 배송 덕분에 행사 전날 받을 수 있었어요. 너무 감사해요.",
    "조립이 간단하고 설명서가 친절하게 나와 있어서 혼자서도 쉽게 했어요.",
    "내용물이 신선하고 맛이 정말 좋아요. 재주문 확정입니다.",
    "화질이 선명하고 색감이 자연스러워서 영상 작업에 딱이에요.",
    "무게가 가볍고 그립감이 좋아서 장시간 사용해도 피로하지 않아요.",
    "반응속도가 빠르고 정확해서 게임할 때 너무 좋아요.",
    "제품이 튼튼하고 방수 기능도 완벽합니다. 매우 추천해요.",
    "충전 속도가 빠르고 호환성도 좋아서 편리하게 사용 중입니다.",
]

NEGATIVE_REVIEWS = [
    "배송이 너무 오래 걸려서 실망했어요. 일주일 넘게 기다렸습니다.",
    "제품 사진과 실제 색상이 너무 달라요. 반품 고려 중입니다.",
    "포장이 엉망이라 제품이 손상된 채로 도착했어요. 품질관리 좀 해주세요.",
    "고객센터 연결이 너무 어렵고 답변도 너무 늦어요.",
    "내구성이 너무 약해서 일주일 만에 부서졌어요. 환불 요청했습니다.",
    "사이즈가 표기와 달라서 교환 신청했는데 처리가 너무 오래 걸려요.",
    "배터리 지속 시간이 설명과 너무 달라요. 2시간도 안 되네요.",
    "불량품이 왔는데 교환 처리가 너무 복잡하고 시간이 오래 걸렸어요.",
    "가격에 비해 품질이 너무 떨어집니다. 기대 이하였어요.",
    "소음이 너무 심해서 사용하기 불편합니다. 반품했어요.",
    "피부에 트러블이 생겨서 사용을 중단했습니다. 성분 확인 필요해요.",
    "화면 터치감이 너무 둔감해서 사용이 불편합니다.",
    "세탁 후 색이 다 빠졌어요. 세탁 주의 표시가 없었습니다.",
    "무게가 너무 무거워서 장시간 착용하기 힘들어요.",
    "발열이 심해서 오래 사용하지 못하겠어요.",
]

NEUTRAL_REVIEWS = [
    "평범한 제품이에요. 딱히 좋지도 나쁘지도 않습니다.",
    "기대했던 것과 조금 달랐지만 그냥 쓸 만한 수준이에요.",
    "가격 대비 보통 정도의 품질입니다. 무난하게 쓸 수 있어요.",
    "배송은 빠른 편이었고 제품은 설명대로 왔어요.",
    "특별히 좋거나 나쁜 점은 없어요. 그냥 평범합니다.",
    "처음엔 좋았는데 시간이 지나니 약간 아쉬운 부분이 생겼어요.",
    "사용하는데 불편함은 없어요. 무난한 제품입니다.",
    "생각보다 크기가 작았지만 기능적으로는 문제없어요.",
    "가격이 조금 비싸지만 품질은 적당합니다.",
    "포장은 잘 되어 있었고 제품도 온전히 도착했습니다.",
]


async def fetch_embeddings(contents: list[str]) -> list[list[float]]:
    """100개 텍스트를 OpenAI batch call 한 번으로 임베딩 생성."""
    if not settings.OPENAI_API_KEY:
        print("OPENAI_API_KEY 미설정 — 랜덤 벡터로 대체 (dev mode)")
        return [[random.uniform(-1, 1) for _ in range(1536)] for _ in contents]

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    response = await client.embeddings.create(
        model="text-embedding-3-small",
        input=contents,
    )
    # API 응답은 index 순서 보장
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


async def seed():
    pool = (
        [random.choice(POSITIVE_REVIEWS) for _ in range(50)]
        + [random.choice(NEGATIVE_REVIEWS) for _ in range(30)]
        + [random.choice(NEUTRAL_REVIEWS) for _ in range(20)]
    )
    random.shuffle(pool)

    ratings_by_sentiment = {
        POSITIVE_REVIEWS[0]: [4.0, 4.5, 5.0],
        NEGATIVE_REVIEWS[0]: [1.0, 1.5, 2.0, 2.5],
        NEUTRAL_REVIEWS[0]:  [3.0, 3.5],
    }

    def pick_rating(content: str) -> float:
        if content in POSITIVE_REVIEWS:
            return random.choice([4.0, 4.5, 5.0])
        if content in NEGATIVE_REVIEWS:
            return random.choice([1.0, 1.5, 2.0, 2.5])
        return random.choice([3.0, 3.5])

    print(f"OpenAI batch embedding 요청 중 ({len(pool)}개)...")
    vectors = await fetch_embeddings(pool)
    print("임베딩 수신 완료. DB 저장 중...")

    engine = create_async_engine(_build_tenant_db_url(TENANT_ID), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    reviews = [
        Review(
            order_id=f"emb-seed-{i+1:03d}",
            content=content,
            rating=pick_rating(content),
            embedding=vector,
            created_at=datetime.now(KST),
        )
        for i, (content, vector) in enumerate(zip(pool, vectors))
    ]

    async with factory() as db:
        db.add_all(reviews)
        await db.commit()

    await engine.dispose()
    print(f"완료 — tenant_id={TENANT_ID}에 리뷰 {len(reviews)}개 삽입 (임베딩 포함)")
    print("  긍정 50개 / 부정 30개 / 중립 20개")


if __name__ == "__main__":
    asyncio.run(seed())
