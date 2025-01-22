"""
Application-wide settings.
"""

import os
from typing import Optional
import redis.asyncio as redis
from opensearchpy import AsyncOpenSearch
from tweepy.asynchronous import AsyncClient
from pydantic_settings import BaseSettings
from squad.aiosession import SessionManager


class Settings(BaseSettings):
    # PG
    sqlalchemy: str = os.getenv(
        "POSTGRESQL", "postgresql+asyncpg://user:password@postgres:5432/squad"
    )
    db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "256"))
    db_overflow: int = int(os.getenv("DB_OVERFLOW", "32"))

    # AES secret
    aes_secret: str = os.getenv(
        "AES_SECRET", "5692fd23e56b9f10f7d6223ebdbd26580ce04fec21a966891d34c9f7f28d9413"
    )

    # Redis.
    redis_url: str = os.getenv("REDIS_URL", "redis://:redispassword@redis:6379/0")
    redis_client: Optional[redis.Redis] = (
        redis.Redis.from_url(os.getenv("REDIS_URL", "redis://:redispassword@redis:6379/0"))
        if os.getenv("REDIS_URL")
        else None
    )

    # Clients.
    tweepy_client: Optional[AsyncClient] = (
        AsyncClient(os.getenv("X_API_TOKEN")) if os.getenv("X_API_TOKEN") else None
    )
    opensearch_client: Optional[AsyncOpenSearch] = (
        AsyncOpenSearch(os.getenv("OPENSEARCH_URL", "http://opensearch:9200"))
        if os.getenv("OPENSEARCH_URL")
        else None
    )
    brave_sm: Optional[SessionManager] = (
        SessionManager(
            headers={"X-Subscription-Token": os.getenv("BRAVE_API_TOKEN")},
            base_url="https://api.search.brave.com",
        )
        if os.getenv("BRAVE_API_TOKEN")
        else None
    )

    # Squad API.
    squad_api_base_url: str = os.getenv("SQUAD_API_BASE_URL", "http://api:8000")

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
