from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    db_host: str = Field(default='localhost', alias='DB_HOST')
    db_port: int = Field(default=5432, alias='DB_PORT')
    db_name: str = Field(default='n8n', alias='DB_NAME')
    db_user: str = Field(default='postgres', alias='DB_USER')
    db_password: str = Field(default='', alias='DB_PASSWORD')
    db_sslmode: str = Field(default='disable', alias='DB_SSLMODE')
    db_schema: str = Field(default='invoice', alias='DB_SCHEMA')
    tz: str = Field(default='UTC', alias='TZ')

    model_config = SettingsConfigDict(
        env_file='.env',
        extra='ignore',
        case_sensitive=False,
    )

    @property
    def db_url(self) -> str:
        user = quote_plus(self.db_user)
        password = quote_plus(self.db_password) if self.db_password else ''
        auth_part = f"{user}:{password}" if password else user
        return f"postgresql+asyncpg://{auth_part}@{self.db_host}:{self.db_port}/{self.db_name}"


@lru_cache(maxsize=1)
def get_settings() -> 'Settings':
    return Settings()  # pragma: no cover


settings = get_settings()
