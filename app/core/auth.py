# API 키 인증 — Redis 캐시 우선 조회 후 main_db fallback / JWT Bearer 검증
import json
import logging
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_main_db
from app.models.main import Tenant

logger = logging.getLogger(__name__)

_redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

CACHE_TTL = 300  # seconds


class TenantData(BaseModel):
    id: int
    name: str
    api_key: str
    plan: str
    is_active: bool


async def get_tenant_by_api_key(api_key: str, db: AsyncSession) -> TenantData | None:
    cache_key = f"tenant:api_key:{api_key}"

    cached = await _redis.get(cache_key)
    if cached:
        return TenantData.model_validate_json(cached)

    result = await db.execute(
        select(Tenant).where(Tenant.api_key == api_key, Tenant.is_active == True)
    )
    tenant = result.scalar_one_or_none()

    if tenant:
        data = TenantData(
            id=tenant.id,
            name=tenant.name,
            api_key=tenant.api_key,
            plan=tenant.plan,
            is_active=tenant.is_active,
        )
        await _redis.setex(cache_key, CACHE_TTL, data.model_dump_json())
        return data

    return None


async def verify_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_main_db),
) -> TenantData:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key missing")

    tenant = await get_tenant_by_api_key(x_api_key, db)
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return tenant


_http_bearer = HTTPBearer(auto_error=False)


async def verify_jwt(
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
) -> TenantData:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    try:
        payload = jwt.decode(credentials.credentials, settings.JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    return TenantData(
        id=tenant_id,
        name=payload.get("tenant_name", ""),
        api_key=payload.get("api_key", ""),
        plan=payload.get("plan", ""),
        is_active=payload.get("is_active", True),
    )
