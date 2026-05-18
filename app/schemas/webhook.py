# 웹훅 요청/응답 스키마
from pydantic import BaseModel, Field


class WebhookRequest(BaseModel):
    """주문 완료 웹훅 페이로드. 동일 order_id는 24시간 내 중복 수신이 자동 차단됩니다."""

    order_id: str = Field(description="쇼핑몰 주문 ID (중복 수신 차단 기준 키)")
    customer_phone: str = Field(description="리뷰 요청 알림을 받을 고객 전화번호 (예: 010-1234-5678)")
    product_name: str = Field(description="구매한 상품명 (알림 메시지에 포함됨)")


class WebhookResponse(BaseModel):
    """웹훅 처리 결과."""

    status: str = Field(description="처리 결과 — accepted(정상 접수) / duplicated(중복 요청 차단)")
