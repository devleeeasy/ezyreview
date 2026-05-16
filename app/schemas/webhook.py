# 웹훅 요청/응답 스키마
from pydantic import BaseModel


class WebhookRequest(BaseModel):
    order_id: str
    customer_phone: str
    product_name: str


class WebhookResponse(BaseModel):
    status: str
