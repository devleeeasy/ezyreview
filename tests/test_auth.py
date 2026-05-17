# JWT 토큰 발급 엔드포인트 테스트
import pytest


@pytest.mark.asyncio
async def test_issue_token_success(client):
    """유효한 API 키 → JWT 반환."""
    resp = await client.post("/auth/token", json={"api_key": "test-api-key-001"})
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 86400


@pytest.mark.asyncio
async def test_issue_token_invalid_key(client):
    """잘못된 API 키 → 401 반환."""
    resp = await client.post("/auth/token", json={"api_key": "wrong-key"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_issue_token_missing_key(client):
    """api_key 필드 누락 → 422 반환."""
    resp = await client.post("/auth/token", json={})
    assert resp.status_code == 422
