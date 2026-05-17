# 리뷰 등록 엔드포인트 테스트
import pytest
import uuid


@pytest.mark.asyncio
async def test_create_review_success(client, auth_headers, mock_celery_tasks):
    """주문 존재 → 리뷰 등록 성공."""
    order_id = f"review-order-{uuid.uuid4().hex[:8]}"

    # 주문 먼저 생성
    await client.post(
        "/webhook/test-api-key-001",
        json={"order_id": order_id, "customer_phone": "test@example.com", "product_name": "리뷰 테스트 상품"},
    )

    resp = await client.post(
        "/reviews",
        headers=auth_headers,
        json={"order_id": order_id, "content": "너무 좋아요!", "rating": 5.0},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["order_id"] == order_id
    assert "review_id" in body
    mock_celery_tasks["analytics"].assert_called_once()


@pytest.mark.asyncio
async def test_create_review_order_not_found(client, auth_headers):
    """존재하지 않는 order_id → 404 반환."""
    resp = await client.post(
        "/reviews",
        headers=auth_headers,
        json={"order_id": "nonexistent-order", "content": "테스트", "rating": 3.0},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_review_duplicate(client, auth_headers, mock_celery_tasks):
    """동일 주문 리뷰 중복 등록 → 409 반환."""
    order_id = f"dup-review-{uuid.uuid4().hex[:8]}"

    await client.post(
        "/webhook/test-api-key-001",
        json={"order_id": order_id, "customer_phone": "test@example.com", "product_name": "중복 테스트"},
    )
    await client.post(
        "/reviews",
        headers=auth_headers,
        json={"order_id": order_id, "content": "첫 번째 리뷰", "rating": 4.0},
    )

    resp = await client.post(
        "/reviews",
        headers=auth_headers,
        json={"order_id": order_id, "content": "두 번째 리뷰", "rating": 3.0},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_review_invalid_rating(client, auth_headers):
    """평점 범위 초과 → 422 반환."""
    resp = await client.post(
        "/reviews",
        headers=auth_headers,
        json={"order_id": "any-order", "content": "테스트", "rating": 6.0},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_review_no_auth(client):
    """인증 헤더 없음 → 401 반환."""
    resp = await client.post(
        "/reviews",
        json={"order_id": "any-order", "content": "테스트", "rating": 3.0},
    )
    assert resp.status_code == 401
