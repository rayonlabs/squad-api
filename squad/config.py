"""
Application-wide settings.
"""

import os
import aioboto3
import redis.asyncio as redis
from boto3.session import Config
from contextlib import asynccontextmanager
from typing import Optional
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

    # S3/object store.
    aws_access_key_id: str = os.getenv("AWS_ACCESS_KEY_ID", "REPLACEME")
    aws_secret_access_key: str = os.getenv("AWS_SECRET_ACCESS_KEY", "REPLACEME")
    aws_endpoint_url: Optional[str] = os.getenv("AWS_ENDPOINT_URL", "http://minio:9000")
    aws_region: str = os.getenv("AWS_REGION", "local")
    storage_bucket: str = os.getenv("STORAGE_BUCKET", "squad")

    @property
    def s3_session(self) -> aioboto3.Session:
        session = aioboto3.Session(
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            region_name=self.aws_region,
        )
        return session

    @asynccontextmanager
    async def s3_client(self):
        session = self.s3_session
        async with session.client(
            "s3",
            endpoint_url=self.aws_endpoint_url,
            config=Config(signature_version="s3v4"),
        ) as client:
            yield client

    # JWT private key for chutes auth.
    jwt_private: bytes = (
        b""
        if not os.getenv("JWT_PRIVATE_PATH")
        else open(os.getenv("JWT_PRIVATE_PATH"), "rb").read()
    )
    jwt_public: bytes = (
        b"" if not os.getenv("JWT_PUBLIC_PATH") else open(os.getenv("JWT_PUBLIC_PATH"), "rb").read()
    )
    dev_auth: Optional[str] = os.getenv("DEV_CHUTES_AUTH")

    # Default for agent max steps.
    default_max_steps: int = int(os.getenv("DEFAULT_MAX_STEPS", "25"))

    # Default user ID for calling chutes, e.g. for X stream index embeddings.
    default_user_id: str = os.getenv("DEFAULT_CHUTES_USER", "dff3e6bb-3a6b-5a2b-9c48-da3abcd5ca5f")

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
    chutes_sm: SessionManager = SessionManager(
        base_url=os.getenv("CHUTES_API_URL", "https://api.chutes.ai"),
    )

    # X OAuth2 stuff.
    x_client_id: Optional[str] = os.getenv("X_CLIENT_ID")
    x_client_secret: Optional[str] = os.getenv("X_CLIENT_SECRET")
    x_api_callback_url: Optional[str] = (
        os.getenv("SQUAD_API_BASE_URL", "http://127.0.0.1:8000") + "/x/callback"
    )

    # Squad URLs.
    squad_base_url: str = os.getenv("SQUAD_BASE_URL", "https://squad.game")
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
