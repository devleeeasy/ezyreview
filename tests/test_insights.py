# 인사이트 API 테스트
import pytest


@pytest.mark.asyncio
async def test_summary_success(client, auth_headers):
    """인증 성공 → summary 응답 구조 확인."""
    resp = await client.get("/insights/summary", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "total_reviews" in body
    assert "avg_rating" in body
    assert "sentiment" in body
    sentiment = body["sentiment"]
    assert all(k in sentiment for k in ("positive", "negative", "neutral", "unanalyzed"))


@pytest.mark.asyncio
async def test_summary_no_auth(client):
    """인증 헤더 없음 → 401 반환."""
    resp = await client.get("/insights/summary")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_reviews_list_success(client, auth_headers):
    """리뷰 목록 조회 → 페이지네이션 구조 확인."""
    resp = await client.get("/insights/reviews", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body
    assert "items" in body
    assert isinstance(body["items"], list)


@pytest.mark.asyncio
async def test_reviews_list_sentiment_filter(client, auth_headers):
    """sentiment 필터 파라미터 → 정상 응답."""
    for sentiment in ("positive", "negative", "neutral"):
        resp = await client.get(f"/insights/reviews?sentiment={sentiment}", headers=auth_headers)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_reviews_list_invalid_sentiment(client, auth_headers):
    """잘못된 sentiment 값 → 422 반환."""
    resp = await client.get("/insights/reviews?sentiment=unknown", headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_reviews_list_pagination(client, auth_headers):
    """limit / offset 파라미터 → 정상 동작."""
    resp = await client.get("/insights/reviews?limit=5&offset=0", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["items"]) <= 5
