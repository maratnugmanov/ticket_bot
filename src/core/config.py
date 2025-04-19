from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, HttpUrl


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_name: str = Field(alias="DATABASE_NAME")
    token: str = Field(alias="TOKEN")
    bot_name: str = Field(alias="BOT_NAME")

    log_level: str = Field(default="info", alias="LOG_LEVEL")
    echo_sql: bool = Field(True, alias="ECHO_SQL")

    telegram_api_base: HttpUrl = HttpUrl("https://api.telegram.org")

    user_default_timezone: str = Field(alias="USER_DEFAULT_TIMEZONE")

    admin_telegram_uid: int = Field(alias="ADMIN_TELEGRAM_UID")
    admin_first_name: str = Field(alias="ADMIN_FIRST_NAME")
    admin_last_name: str = Field(alias="ADMIN_LAST_NAME")
    admin_timezone: str = Field(alias="ADMIN_TIMEZONE")

    manager_telegram_uid: int = Field(alias="MANAGER_TELEGRAM_UID")
    manager_first_name: str = Field(alias="MANAGER_FIRST_NAME")
    manager_last_name: str = Field(alias="MANAGER_LAST_NAME")
    manager_timezone: str = Field(alias="MANAGER_TIMEZONE")

    def get_tg_endpoint(self, method: str) -> str:
        """Constructs the full Telegram API endpoint URL for a given method."""
        return f"{self.telegram_api_base}/bot{self.token}/{method}"


settings = Settings()
