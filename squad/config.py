"""
Application-wide settings.
"""

import os
import aioboto3
import aiomcache
from functools import lru_cache
import redis.asyncio as redis
from boto3.session import Config
from contextlib import asynccontextmanager
from typing import Optional, Any
from opensearchpy import AsyncOpenSearch
from tweepy.asynchronous import AsyncClient
from pydantic_settings import BaseSettings
from squad.aiosession import SessionManager
from kubernetes import client
from kubernetes.config import load_kube_config, load_incluster_config


def create_kubernetes_client(cls: Any = client.CoreV1Api):
    """
    Create a k8s client.
    """
    try:
        if os.getenv("KUBERNETES_SERVICE_HOST") is not None:
            load_incluster_config()
        else:
            load_kube_config(config_file=os.getenv("KUBECONFIG"))
        return cls()
    except Exception as exc:
        raise Exception(f"Failed to create Kubernetes client: {str(exc)}")


@lru_cache(maxsize=1)
def k8s_core_client():
    return create_kubernetes_client()


@lru_cache(maxsize=1)
def k8s_app_client():
    return create_kubernetes_client(cls=client.AppsV1Api)


@lru_cache(maxsize=1)
def k8s_job_client():
    return create_kubernetes_client(cls=client.BatchV1Api)


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

    # Memcached.
    memcache: Optional[aiomcache.Client] = (
        aiomcache.Client(os.getenv("MEMCACHED", "memcached"), 11211)
        if os.getenv("MEMCACHED")
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
            config=Config(
                signature_version="s3v4",
                s3={"use_accelerate_endpoint": False, "addressing_style": "path"},
            ),
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
    data_universe_sm: Optional[SessionManager] = (
        SessionManager(
            headers={"X-API-KEY": os.getenv("DATA_UNIVERSE_API_KEY")},
            base_url=os.getenv("DATA_UNIVERSE_BASE_URL", "https://sn13.api.macrocosmos.ai"),
        )
        if os.getenv("DATA_UNIVERSE_API_KEY")
        else None
    )
    apex_search_sm: Optional[SessionManager] = (
        SessionManager(
            headers={"api-key": os.getenv("APEX_SEARCH_API_KEY")},
            base_url=os.getenv("APEX_SEARCH_BASE_URL", "https://sn1.api.macrocosmos.ai"),
        )
        if os.getenv("APEX_SEARCH_API_KEY")
        else None
    )

    # Context size limits.
    default_context_size: int = int(os.getenv("DEFAULT_CONTEXT_SIZE", "128000"))

    # X OAuth2 stuff.
    x_client_id: Optional[str] = os.getenv("X_CLIENT_ID")
    x_client_secret: Optional[str] = os.getenv("X_CLIENT_SECRET")
    x_api_callback_url: Optional[str] = (
        os.getenv("SQUAD_API_BASE_URL", "http://127.0.0.1:8000") + "/x/callback"
    )
    x_access_token: Optional[str] = os.getenv("X_ACCESS_TOKEN")
    x_access_token_secret: Optional[str] = os.getenv("X_ACCESS_TOKEN_SECRET")
    x_consumer_token: Optional[str] = os.getenv("X_CONSUMER_TOKEN")
    x_consumer_token_secret: Optional[str] = os.getenv("X_CONSUMER_TOKEN_SECRET")

    # Squad URLs.
    squad_base_url: str = os.getenv("SQUAD_BASE_URL", "https://squad.ai")
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

    # Account limits.
    default_limit_max_steps: int = int(os.getenv("LIMIT_MAX_STEPS", "5"))
    default_limit_max_execution_time: int = int(os.getenv("LIMIT_MAX_EXECUTION_TIME", "300"))
    default_limit_max_invocations: int = int(os.getenv("LIMIT_MAX_INVOCATIONS", "48"))
    default_limit_max_invocations_window: int = int(
        os.getenv("LIMIT_MAX_INVOCATION_WINDOW", str(24 * 60 * 60))
    )
    default_limit_max_tools: int = int(os.getenv("LIMIT_MAX_TOOLS", "5"))
    default_limit_max_agents: int = int(os.getenv("LIMIT_MAX_AGENTS", "1"))
    default_limit_max_agent_tools: int = int(os.getenv("LIMIT_MAX_AGENT_TOOLS", "5"))
    default_allowed_models: list[str] = [
        "unsloth/gemma-3-27b-it",
        "mistralai/Mistral-Small-3.1-24B-Instruct-2503",
        "deepseek-ai/DeepSeek-R1-Distill-Llama-70B",
        "unsloth/Llama-3.3-70B-Instruct",
        "chutesai/Llama-4-Scout-17B-16E-Instruct",
        "chutesai/Llama-4-Maverick-17B-128E-Instruct-FP8",
    ]

    # Misc.
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"


settings = Settings()
