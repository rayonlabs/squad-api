"""
Application-wide settings.
"""

import os
import redis.asyncio as redis
from opensearchpy import AsyncOpenSearch
from tweepy.asynchronous import AsyncClient
from pydantic_settings import BaseSettings
from squad.aiosession import SessionManager


class Settings(BaseSettings):
    # PG
    sqlalchemy: str = os.getenv(
        "POSTGRESQL", "postgresql+asyncpg://user:password@127.0.0.1:5432/chutes"
    )
    db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "256"))
    db_overflow: int = int(os.getenv("DB_OVERFLOW", "32"))

    # Redis.
    redis_url: str = os.getenv("REDIS_URL", "redis://:redispassword@127.0.0.1:6379/0")
    redis_client: redis.Redis = redis.Redis.from_url(
        os.getenv("REDIS_URL", "redis://:redispassword@127.0.0.1:6379/0")
    )

    # Clients.
    tweepy_client: AsyncClient = AsyncClient(os.getenv("X_API_TOKEN"))
    opensearch_client: AsyncOpenSearch = AsyncOpenSearch(
        os.getenv("OPENSEARCH", "http://127.0.0.1:9200")
    )
    brave_sm: SessionManager = SessionManager(
        headers={"X-Subscription-Token": os.getenv("BRAVE_API_TOKEN")},
        base_url="https://api.search.brave.com",
    )

    # Squad API.
    squad_api_base_url: str = os.getenv("SQUAD_API_BASE_URL", "http://127.0.0.1:8000")

    # Tweet storage.
    tweet_index_version: int = int(os.getenv("TWEET_INDEX_VERSION", "0"))
    tweet_index_shards: int = int(os.getenv("TWEET_INDEX_SHARDS", "1"))
    tweet_index_replicas: int = int(os.getenv("TWEET_INDEX_REPLICAS", "0"))
    tweet_embed_weight: float = float(os.getenv("TWEET_EMBED_WEIGHT", "0.5"))

    # Arbitrary memory storage.
    memory_index_version: int = int(os.getenv("MEMORY_INDEX_VERSION", "0"))
    memory_index_shards: int = int(os.getenv("MEMORY_INDEX_SHARDS", "1"))
    memory_index_replicas: int = int(os.getenv("MEMORY_INDEX_REPLICAS", "1"))
    memory_embed_weight: float = float(os.getenv("MEMORY_EMBED_WEIGHT", "0.5"))

    # Misc.
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"


settings = Settings()
