# 환경변수 기반 설정 — pydantic-settings로 .env 자동 로딩
import re
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    # 미설정 시 DATABASE_URL에서 asyncpg 드라이버 제거 + 유지보수 DB로 자동 유도
    POSTGRES_MAINTENANCE_URL: str = ""
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET: str = "change-me-in-production"
    OPENAI_API_KEY: str = ""
    KAKAO_API_KEY: str = ""
    GMAIL_USER: str = ""        # Gmail 발신 계정 (예: yourname@gmail.com)
    GMAIL_APP_PASSWORD: str = ""  # Gmail 앱 비밀번호 (2단계 인증 후 발급)

    model_config = {"env_file": ".env", "extra": "ignore"}

    def model_post_init(self, __context: object) -> None:
        # Railway는 postgresql:// 형식으로 제공 — asyncpg 드라이버로 자동 변환
        db_url = self.DATABASE_URL
        if db_url.startswith("postgresql://") and "+asyncpg" not in db_url:
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            object.__setattr__(self, "DATABASE_URL", db_url)

        if not self.POSTGRES_MAINTENANCE_URL:
            # asyncpg 드라이버 제거한 URL을 maintenance용으로 사용
            maint_url = re.sub(r"postgresql\+asyncpg://", "postgresql://", db_url)
            object.__setattr__(self, "POSTGRES_MAINTENANCE_URL", maint_url)


settings = Settings()
