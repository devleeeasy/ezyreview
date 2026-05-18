# API 키 → JWT 교환 엔드포인트
import zoneinfo
from datetime import datetime, timedelta

from jose import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_by_api_key
from app.core.config import settings
from app.core.db import get_main_db

router = APIRouter(prefix="/auth", tags=["auth"])

KST = zoneinfo.ZoneInfo("Asia/Seoul")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24


class TokenRequest(BaseModel):
    """JWT 발급 요청. 테넌트 등록 시 발급받은 API 키를 전달합니다."""

    api_key: str = Field(description="테넌트 등록 시 발급받은 API 키")


class TokenResponse(BaseModel):
    """JWT 발급 응답. access_token을 관리 API 요청 시 Authorization: Bearer 헤더에 포함하세요."""

    access_token: str = Field(description="발급된 JWT 액세스 토큰")
    token_type: str = Field(default="bearer", description="토큰 타입 (항상 bearer)")
    expires_in: int = Field(default=JWT_EXPIRE_HOURS * 3600, description="토큰 만료 시간 (초 단위, 기본 24시간)")


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="JWT 토큰 발급",
    description="API 키를 전달하면 24시간 유효한 JWT 액세스 토큰을 반환합니다.",
)
async def issue_token(
    body: TokenRequest,
    db: AsyncSession = Depends(get_main_db),
) -> TokenResponse:
    tenant = await get_tenant_by_api_key(body.api_key, db)
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid API key")

    now = datetime.now(KST)
    payload = {
        "sub": str(tenant.id),
        "tenant_id": tenant.id,
        "tenant_name": tenant.name,
        "api_key": tenant.api_key,
        "plan": tenant.plan,
        "is_active": tenant.is_active,
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)
    return TokenResponse(access_token=token)
