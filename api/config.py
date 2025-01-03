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
    redis_url: str = os.getenv("REDIS_URL", "redis://:redispassword@127.0.0.1:6379/0")
    redis_client: redis.Redis = redis.Redis.from_url(
        os.getenv("REDIS_URL", "redis://:redispassword@127.0.0.1:6379/0")
    )
    db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "256"))
    db_overflow: int = int(os.getenv("DB_OVERFLOW", "32"))
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    tweepy_client: AsyncClient = AsyncClient(os.getenv("X_API_TOKEN"))
    opensearch_client: AsyncOpenSearch = AsyncOpenSearch(os.getenv("OPENSEARCH", "http://127.0.0.1:9200"))
    tweets_index_version: int = int(os.getenv("TWEET_INDEX_VERSION", "0"))
    brave_index_version: int = int(os.getenv("BRAVE_INDEX_VERSION", "0"))
    memory_index_version: int = int(os.getenv("MEMORY_INDEX_VERSION", "0"))


settings = Settings()
