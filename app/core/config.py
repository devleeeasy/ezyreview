# 환경변수 기반 설정 — pydantic-settings로 .env 자동 로딩
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    POSTGRES_MAINTENANCE_URL: str
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET: str = "change-me-in-production"
    OPENAI_API_KEY: str = ""
    KAKAO_API_KEY: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
