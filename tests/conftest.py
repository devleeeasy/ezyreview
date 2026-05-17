# 테스트 픽스처 — FastAPI 앱, 외부 의존성 mock, 이메일 발송 제한
import pytest
import pytest_asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.main import app
from app.core.config import settings
from app.core.db import get_main_db, _build_tenant_db_url

TEST_API_KEY = "test-api-key-001"

_email_send_count = 0
_EMAIL_SEND_LIMIT = 3


# ---------- DB: 테스트용 NullPool 세션 ----------

async def _test_main_db() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@asynccontextmanager
async def _test_tenant_session(tenant_id: int) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(_build_tenant_db_url(tenant_id), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------- 픽스처 ----------

@pytest_asyncio.fixture(scope="session")
async def client():
    """FastAPI 테스트 클라이언트 — DB 의존성을 NullPool로 교체."""
    app.dependency_overrides[get_main_db] = _test_main_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def mock_tenant_session():
    """get_tenant_session을 NullPool 버전으로 교체, create_tenant_db는 no-op으로 처리."""
    async def _noop_create_tenant_db(tenant_id: int) -> None:
        pass

    with patch("app.api.webhook.get_tenant_session", _test_tenant_session), \
         patch("app.api.webhook.create_tenant_db", _noop_create_tenant_db), \
         patch("app.api.reviews.get_tenant_session", _test_tenant_session), \
         patch("app.api.insights.get_tenant_session", _test_tenant_session):
        yield


@pytest.fixture(autouse=True)
def mock_redis():
    """Redis 싱글톤 mock — SET NX 상태를 인메모리로 시뮬레이션."""
    store: dict = {}

    async def _get(key): return store.get(key)
    async def _setex(key, ttl, value): store[key] = value
    async def _set(key, value, ex=None, nx=None):
        if nx and key in store:
            return False  # NX: 이미 존재하면 실패
        store[key] = value
        return True
    async def _exists(key): return key in store

    with patch("app.core.auth._redis") as mock_auth, \
         patch("app.api.webhook._redis") as mock_webhook:
        for m in (mock_auth, mock_webhook):
            m.get = AsyncMock(side_effect=_get)
            m.setex = AsyncMock(side_effect=_setex)
            m.set = AsyncMock(side_effect=_set)
            m.exists = AsyncMock(side_effect=_exists)
        yield


@pytest.fixture(autouse=True)
def mock_celery_tasks():
    """Celery 태스크 실제 실행 방지."""
    with patch("worker.tasks.review_request_task.delay") as mock_rr, \
         patch("worker.tasks.analytics_task.delay") as mock_an:
        mock_rr.return_value = MagicMock()
        mock_an.return_value = MagicMock()
        yield {"review_request": mock_rr, "analytics": mock_an}


@pytest.fixture(autouse=True)
def mock_email(request):
    """이메일 발송 mock — real_email 마커 없으면 실제 발송 차단."""
    global _email_send_count

    if request.node.get_closest_marker("real_email"):
        if _email_send_count >= _EMAIL_SEND_LIMIT:
            pytest.skip(f"이메일 발송 한도({_EMAIL_SEND_LIMIT}건) 초과 — 테스트 스킵")
        _email_send_count += 1
        yield
    else:
        with patch("worker.review_request._send_via_sendgrid", return_value=(True, None)), \
             patch("worker.review_request._send_via_gmail", return_value=(True, None)):
            yield


@pytest.fixture
def auth_headers():
    return {"X-Api-Key": TEST_API_KEY}
