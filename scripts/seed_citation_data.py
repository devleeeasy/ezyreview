# AI 인용 모니터링 MVP seed — 카테고리/브랜드/브랜드 별칭/질의 삽입
# 사용법: python scripts/seed_citation_data.py [카테고리명]  (미지정 시 노트북)
import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import _build_tenant_db_url
from app.models.citation import Brand, BrandAlias, Category, Query

TENANT_ID = 1

# 카테고리별 seed 데이터 — 새 카테고리 추가 시 이 dict에만 항목을 늘리면 됨
CATEGORIES = {
    "노트북": {
        "brands": ["삼성전자", "LG전자", "애플", "레노버", "에이수스"],
        "aliases": {
            "LG전자": ["LG 그램", "그램"],
            "삼성전자": ["갤럭시북", "삼성"],
            "애플": ["맥북", "맥북에어", "맥북프로"],
        },
        "queries": [
            "출장용으로 가벼운 노트북 추천해줘",
            "대학생 인강 듣기 좋은 노트북 뭐가 있어?",
            "배터리 오래가는 노트북 뭐가 있을까",
            "개발자한테 좋은 노트북 추천",
            "100만원대 가성비 노트북 뭐가 좋아?",
            "영상 편집용 노트북 추천해줘",
            "무게 1kg 이하 노트북 있어?",
            "국내 AS 잘되는 노트북 브랜드는?",
            "맥북이랑 갤럭시북 중에 뭐가 나아?",
            "게이밍 겸용 가능한 노트북 추천",
        ],
    },
    "청소기": {
        "brands": ["삼성전자", "LG전자", "다이슨", "샤크", "일렉트로룩스"],
        "aliases": {
            "삼성전자": ["비스포크 제트"],
            "LG전자": ["코드제로", "LG 코드제로"],
        },
        "queries": [
            "무선 청소기 추천해줘",
            "반려동물 키우는 집에 좋은 청소기 뭐가 있어?",
            "흡입력 좋은 청소기 추천",
            "가벼운 무선청소기 뭐가 있을까",
            "먼지통 비우기 편한 청소기 추천해줘",
            "물걸레 겸용 청소기 뭐가 좋아?",
            "층간소음 걱정 없는 조용한 청소기 있어?",
            "1인 가구용 소형 청소기 추천",
            "다이슨이랑 LG 코드제로 중에 뭐가 나아?",
            "가성비 좋은 무선청소기 추천해줘",
        ],
    },
}


async def seed(category_name: str):
    data = CATEGORIES[category_name]
    engine = create_async_engine(_build_tenant_db_url(TENANT_ID), echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        category = Category(tenant_id=TENANT_ID, name=category_name, source="seed")
        db.add(category)
        await db.flush()

        brands = {name: Brand(category_id=category.id, name=name) for name in data["brands"]}
        db.add_all(brands.values())
        await db.flush()

        alias_count = 0
        for brand_name, aliases in data["aliases"].items():
            for alias in aliases:
                db.add(BrandAlias(brand_id=brands[brand_name].id, alias=alias))
                alias_count += 1

        db.add_all([Query(category_id=category.id, text=text) for text in data["queries"]])

        await db.commit()

    print(
        f"Seeded category '{category_name}' — {len(brands)} brands, "
        f"{alias_count} aliases, {len(data['queries'])} queries"
    )
    await engine.dispose()


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "노트북"
    if name not in CATEGORIES:
        print(f"알 수 없는 카테고리: '{name}' (사용 가능: {', '.join(CATEGORIES)})")
        sys.exit(1)
    asyncio.run(seed(name))
