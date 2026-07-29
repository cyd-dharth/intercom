import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("app.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    app_base_url: str = "http://localhost:8000"
    session_secret: str
    database_url: str
    custom_domain_cname_target: str = "ghs.googlehosted.com"

    gemini_api_key: str = ""
    llm_model_primary: str = "gemini-2.0-flash"
    llm_model_fallback: str = "gemini-2.0-flash-lite"
    embedding_model: str = "gemini-embedding-2"
    embedding_dimensions: int = 1536
    llm_timeout_seconds: int = 8
    ai_daily_budget_cents: int = 200

    support_email: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    imap_host: str = "imap.gmail.com"
    imap_poll_seconds: int = 20
    email_fallback_workspace_slug: str = "demo"
    email_domain_for_message_id: str = "inbox.local"

    worker_concurrency: int = 2
    log_level: str = "INFO"

    port: int = 8000
    host: str = "0.0.0.0"

    def ai_enabled(self) -> bool:
        return bool(self.gemini_api_key)

    def email_enabled(self) -> bool:
        return bool(self.smtp_user and self.smtp_password and self.support_email)


def load_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    if not settings.ai_enabled():
        logger.warning("GEMINI_API_KEY not set, AI features will degrade to extractive summaries")
    if not settings.email_enabled():
        logger.warning("Email credentials not set, email channel disabled")
    return settings


settings = load_settings()
