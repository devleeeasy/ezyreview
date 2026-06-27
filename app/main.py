# ezyreview FastAPI 앱 진입점
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.reviews import router as reviews_router
from app.api.insights import router as insights_router
from app.api.tenants import router as tenants_router
from app.api.webhook import router as webhook_router
from app.core.db import init_main_db, migrate_all_tenants

logging.basicConfig(level=logging.DEBUG if settings.IS_DEVELOPMENT_MODE else logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — initializing main_db tables")
    await init_main_db()
    logger.info("main_db ready")
    await migrate_all_tenants()
    yield
    logger.info("Shutting down")


_DESCRIPTION = """
이커머스 쇼핑몰의 주문 완료 웹훅을 수신하여 리뷰 요청 알림을 자동 발송하고,
AI로 리뷰를 분석하는 **멀티테넌트 SaaS 백엔드**입니다.

![Python](https://img.shields.io/badge/Python_3.11-3776AB?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=flat&logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-DC382D?style=flat&logo=redis&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-37814A?style=flat&logo=celery&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-412991?style=flat&logo=openai&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=white)

## 주요 기능
- **멀티테넌시**: 테넌트별 독립 DB(`tenant_{id}_db`) 자동 생성 및 완전 격리
- **인증**: API 키(웹훅 서버→서버) + JWT Bearer(리뷰·인사이트 관리 API) 용도별 이중 인증
- **웹훅 수신**: 주문 완료 이벤트 수신 → 중복 차단(Redis) → 리뷰 요청 알림 비동기 발송
- **AI 인사이트 (NLU)**: OpenAI 기반 자연어 처리로 리뷰 감성(긍정/부정/중립)을 분석하고 핵심 이슈·강점을 추출
- **의미 기반 검색**: pgvector 코사인 유사도로 리뷰를 벡터화해 자연어 쿼리로 유사 리뷰를 찾는 클라우드 기반 시맨틱 서치
- **주간 리포트**: 비정형 리뷰 텍스트를 AI로 요약·분석한 주간 인사이트 리포트 자동 생성 및 이메일 발송

## 데모 시연 순서

**1단계 — 테넌트 등록 & 인증**
`POST /tenants` 로 테넌트를 등록한 뒤 `POST /auth/token` 으로 JWT를 발급받습니다.

**2단계 — 샘플 데이터 생성** *(DB가 비어있을 때)*
`POST /admin/seed-test-data/{tenant_id}` 로 주문·리뷰·분석 결과를 한번에 생성합니다.
OpenAI API를 호출하지 않으므로 즉시 실행됩니다.

**3단계 — 인사이트 확인**
`GET /insights/summary` → `GET /insights/search` → `POST /admin/generate-report/{tenant_id}`

---

> **실제 웹훅 시나리오**: `POST /webhook/order-completed` 수신 → Celery가 알림 발송·AI 분석 자동 처리 → 인사이트 확인
"""

_TAGS: list[dict] = [
    {"name": "tenants", "description": "테넌트 등록 및 API 키 관리"},
    {"name": "auth", "description": "테넌트 로그인 및 JWT 토큰 발급"},
    {"name": "webhook", "description": "주문 완료 웹훅 수신 — 테넌트 인증 후 리뷰 요청 알림 발송"},
    {"name": "reviews", "description": "리뷰 목록 조회 및 상세 확인"},
    {"name": "insights", "description": "AI 기반 리뷰 감성 분석, pgvector 의미 검색, 주간 리뷰 요약 리포트"},
    {"name": "admin", "description": "내부 운영용 — 리뷰 배치 분석, 주간 리포트 수동 생성, 테스트 데이터 시드 트리거"},
]

app = FastAPI(
    title="Ezyreview",
    version="1.0.0",
    description=_DESCRIPTION,
    openapi_tags=_TAGS,
    lifespan=lifespan,
)

app.include_router(tenants_router)
app.include_router(auth_router)
app.include_router(webhook_router)
app.include_router(reviews_router)
app.include_router(insights_router)
app.include_router(admin_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
