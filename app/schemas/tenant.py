# 테넌트 요청/응답 스키마
from datetime import datetime

from pydantic import BaseModel, Field


class TenantCreateRequest(BaseModel):
    """쇼핑몰 등록 요청. 등록 완료 시 API 키가 발급됩니다."""

    name: str = Field(description="쇼핑몰(테넌트) 이름")
    plan: str = Field(default="free", description="구독 플랜 — free / basic / pro")


class TenantCreateResponse(BaseModel):
    """쇼핑몰 등록 응답. api_key를 웹훅 URL과 /auth/token 인증에 사용하세요."""

    id: int = Field(description="테넌트 고유 ID")
    name: str = Field(description="쇼핑몰(테넌트) 이름")
    api_key: str = Field(description="발급된 API 키 — 웹훅 URL 및 인증에 사용")
    plan: str = Field(description="구독 플랜")
    created_at: datetime = Field(description="테넌트 등록 일시 (Asia/Seoul)")
