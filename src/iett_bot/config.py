from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: SecretStr = SecretStr("")
    legacy_bot_token: SecretStr = SecretStr("")
    healthcheck_ping_url: SecretStr = SecretStr("")
    database_url: SecretStr = SecretStr("")
    poll_interval_seconds: float = Field(default=60, ge=10)
    poll_concurrency: int = Field(default=2, ge=1, le=10)
    http_timeout_seconds: float = Field(default=10, ge=1, le=60)
    outage_after_seconds: int = Field(default=600, ge=60)
    stale_after_seconds: int = Field(default=180, ge=30)
    history_retention_days: int = Field(default=90, ge=1)
    max_subscriptions: int = Field(default=20, ge=1, le=100)
    health_host: str = "127.0.0.1"
    health_port: int = Field(default=8080, ge=1, le=65535)
    log_level: str = "INFO"

    def require_runtime(self) -> None:
        if not self.bot_token.get_secret_value():
            raise ValueError(
                "Set BOT_TOKEN in .env or the environment (obtain it from @BotFather)."
            )
        if not self.database_url.get_secret_value():
            raise ValueError("Set DATABASE_URL to a PostgreSQL connection URL.")
        if self.legacy_bot_token.get_secret_value() == self.bot_token.get_secret_value():
            raise ValueError("LEGACY_BOT_TOKEN must belong to a different bot than BOT_TOKEN.")
