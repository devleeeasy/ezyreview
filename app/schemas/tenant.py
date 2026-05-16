# 테넌트 요청/응답 스키마
from datetime import datetime

from pydantic import BaseModel


class TenantCreateRequest(BaseModel):
    name: str
    plan: str = "free"


class TenantCreateResponse(BaseModel):
    id: int
    name: str
    api_key: str
    plan: str
    created_at: datetime
