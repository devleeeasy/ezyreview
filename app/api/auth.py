# API 키 → JWT 교환 엔드포인트
import zoneinfo
from datetime import datetime, timedelta

from jose import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_by_api_key
from app.core.config import settings
from app.core.db import get_main_db

router = APIRouter(prefix="/auth", tags=["auth"])

KST = zoneinfo.ZoneInfo("Asia/Seoul")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24


class TokenRequest(BaseModel):
    api_key: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = JWT_EXPIRE_HOURS * 3600


@router.post("/token", response_model=TokenResponse)
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
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)
    return TokenResponse(access_token=token)
