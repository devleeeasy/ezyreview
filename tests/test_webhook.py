# 웹훅 수신 엔드포인트 테스트
import pytest
import uuid


@pytest.mark.asyncio
async def test_webhook_accepted(client, mock_celery_tasks):
    """정상 웹훅 수신 → accepted 반환."""
    order_id = f"test-order-{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        f"/webhook/test-api-key-001",
        json={
            "order_id": order_id,
            "customer_phone": "test@example.com",
            "product_name": "테스트 상품",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    mock_celery_tasks["review_request"].assert_called_once()


@pytest.mark.asyncio
async def test_webhook_duplicate(client):
    """동일 order_id 재전송 → duplicated 반환."""
    order_id = f"test-dup-{uuid.uuid4().hex[:8]}"
    payload = {
        "order_id": order_id,
        "customer_phone": "test@example.com",
        "product_name": "중복 테스트 상품",
    }
    await client.post("/webhook/test-api-key-001", json=payload)
    resp = await client.post("/webhook/test-api-key-001", json=payload)

    assert resp.status_code == 200
    assert resp.json()["status"] == "duplicated"


@pytest.mark.asyncio
async def test_webhook_invalid_api_key(client):
    """잘못된 API 키 → 401 반환."""
    resp = await client.post(
        "/webhook/invalid-key-xyz",
        json={
            "order_id": "order-any",
            "customer_phone": "test@example.com",
            "product_name": "상품",
        },
    )
    assert resp.status_code == 401
