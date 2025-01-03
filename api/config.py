"""
Application-wide settings.
"""

import os
import redis.asyncio as redis
from opensearchpy import AsyncOpenSearch
from tweepy.asynchronous import AsyncClient
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    sqlalchemy: str = os.getenv(
        "POSTGRESQL", "postgresql+asyncpg://user:password@127.0.0.1:5432/chutes"
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    redis_client: redis.Redis = redis.Redis.from_url(
        os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    )
    db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "256"))
    db_overflow: int = int(os.getenv("DB_OVERFLOW", "32"))
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    tweepy_client: AsyncClient = AsyncClient(os.getenv("X_API_TOKEN"))
    opensearch_client: AsyncOpenSearch = AsyncOpenSearch(os.environ["OPENSEARCH"])


settings = Settings()
