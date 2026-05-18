# 리뷰 요청/응답 스키마
from datetime import datetime

from pydantic import BaseModel, Field


class ReviewCreateRequest(BaseModel):
    """리뷰 등록 요청. 해당 order_id가 존재해야 하며, 주문당 리뷰는 1건만 허용됩니다."""

    order_id: str = Field(description="리뷰를 남길 주문 ID")
    content: str = Field(description="리뷰 내용")
    rating: float = Field(description="평점 (1.0 ~ 5.0)", ge=1.0, le=5.0)


class ReviewCreateResponse(BaseModel):
    """리뷰 등록 응답. 등록 즉시 AI 감성 분석 태스크가 비동기로 발행됩니다."""

    review_id: int = Field(description="생성된 리뷰 고유 ID")
    order_id: str = Field(description="주문 ID")
    message: str = Field(description="처리 결과 메시지")
