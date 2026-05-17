# 테넌트 등록 엔드포인트 — 가입 시 API 키 자동 발급
import secrets
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_main_db
from app.models.main import Tenant
from app.schemas.tenant import TenantCreateRequest, TenantCreateResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post(
    "",
    response_model=TenantCreateResponse,
    status_code=201,
    summary="테넌트 등록",
    description="쇼핑몰을 등록하고 API 키를 발급합니다. 발급된 api_key를 웹훅 URL과 인증에 사용하세요.",
)
async def create_tenant(
    body: TenantCreateRequest,
    db: AsyncSession = Depends(get_main_db),
) -> TenantCreateResponse:
    api_key = secrets.token_urlsafe(32)

    tenant = Tenant(name=body.name, api_key=api_key, plan=body.plan)
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)

    logger.info("Tenant created — id=%s name=%s", tenant.id, tenant.name)

    return TenantCreateResponse(
        id=tenant.id,
        name=tenant.name,
        api_key=tenant.api_key,
        plan=tenant.plan,
        created_at=tenant.created_at,
    )
