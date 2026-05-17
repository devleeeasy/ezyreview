# Locust 부하 테스트 — 웹훅 수신 / 중복 차단 / 인사이트 API
import random
import string

from locust import HttpUser, between, task


def _random_order_id() -> str:
    return "load-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))


PRODUCTS = [
    "블루투스 이어폰", "스마트 워치", "무선 마우스", "기계식 키보드",
    "보조배터리", "노트북 거치대", "텀블러", "에어프라이어",
]

# 중복 차단 검증용 고정 order_id
DUPLICATE_ORDER_ID = "load-dedup-fixed-order"

API_KEY = "test-api-key-001"


class EzyreviewUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task(5)
    def webhook_new_order(self) -> None:
        """신규 주문 웹훅 — 매번 다른 order_id로 정상 수신 확인."""
        self.client.post(
            f"/webhook/{API_KEY}",
            json={
                "order_id": _random_order_id(),
                "customer_phone": "010-0000-0000",
                "product_name": random.choice(PRODUCTS),
            },
            name="/webhook/:api_key [new]",
        )

    @task(2)
    def webhook_duplicate_order(self) -> None:
        """중복 order_id 웹훅 — duplicated 응답이어야 정상."""
        resp = self.client.post(
            f"/webhook/{API_KEY}",
            json={
                "order_id": DUPLICATE_ORDER_ID,
                "customer_phone": "010-0000-0000",
                "product_name": "중복 테스트 상품",
            },
            name="/webhook/:api_key [dedup]",
        )
        # 최초 1회는 accepted, 이후는 duplicated — 둘 다 200이므로 에러 아님
        if resp.status_code == 200:
            body = resp.json()
            if body.get("status") not in ("accepted", "duplicated"):
                resp.failure(f"Unexpected status: {body}")

    @task(3)
    def insights_summary(self) -> None:
        """인사이트 요약 API — API 키 헤더 인증."""
        self.client.get(
            "/insights/summary",
            headers={"X-Api-Key": API_KEY},
            name="/insights/summary",
        )
