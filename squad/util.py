"""
Utilities and helpers.
"""

import os
import hashlib
import time
import secrets
import datetime
import transformers
import requests
import traceback
from functools import lru_cache
from loguru import logger
from contextlib import asynccontextmanager
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from sqlalchemy import text
from squad.database import get_session
from squad.config import settings
from squad.aiosession import SessionManager
from squad.auth import generate_auth_token


NSFW_SM = SessionManager(
    base_url="https://chutes-nsfw-classifier.chutes.ai",
)
HATE_SM = SessionManager(
    base_url="https://chutes-hate-speech-detector.chutes.ai",
)
TOKENIZER = transformers.AutoTokenizer.from_pretrained(
    os.path.join(os.path.dirname(__file__), "..", "bge-reranker-large")
)


@lru_cache(maxsize=1)
def _get_chutes_token(_: int):
    return f"Bearer {generate_auth_token(settings.default_user_id, duration_minutes=5)}"


def get_chutes_token():
    now = int(time.time())
    return _get_chutes_token(now - (now % 360))


@asynccontextmanager
async def chutes_get(path, user, **kwargs):
    """
    Perform GET request to chutes API as user.
    """
    async with settings.chutes_sm.get_session() as session:
        if "headers" not in kwargs:
            kwargs["headers"] = {}
        kwargs["headers"]["Authorization"] = f"Bearer {generate_auth_token(user.user_id)}"
        async with session.get(path, **kwargs) as response:
            yield response


@asynccontextmanager
async def chutes_post(path, user, payload, **kwargs):
    """
    Perform POST request to chutes API as user.
    """
    kwargs["json"] = payload
    async with settings.chutes_sm.get_session() as session:
        if "headers" not in kwargs:
            kwargs["headers"] = {}
        kwargs["headers"]["Authorization"] = f"Bearer {generate_auth_token(user.user_id)}"
        async with session.post(path, **kwargs) as response:
            yield response


async def encrypt(secret: str, secret_type: str = "x") -> bytes:
    """
    Encrypt a secret.
    """
    padder = padding.PKCS7(128).padder()
    iv = secrets.token_bytes(16)
    cipher = Cipher(
        algorithms.AES(bytes.fromhex(settings.aes_secret)),
        modes.CBC(iv),
        backend=default_backend(),
    )
    padded_data = padder.update(secret.encode()) + padder.finalize()
    encryptor = cipher.encryptor()
    encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
    async with get_session() as session:
        query = text(
            "SELECT pgp_sym_encrypt(:data, (SELECT key FROM secrets WHERE id = :secret_type)) AS encrypted_data"
        )
        cipher_str = f"{iv.hex()}::::{encrypted_data.hex()}"
        result = await session.execute(query, {"data": cipher_str, "secret_type": secret_type})
        row = result.first()
        return row.encrypted_data.hex()


async def decrypt(encrypted_secret: str, secret_type: str = "x") -> str:
    """
    Decrypt a payment wallet secret.
    """
    encrypted_secret = bytes.fromhex(encrypted_secret)
    async with get_session() as session:
        result = await session.execute(
            text(
                "SELECT pgp_sym_decrypt(:encrypted_data, (SELECT key FROM secrets WHERE id = :secret_type)) AS decrypted_data"
            ),
            {"encrypted_data": encrypted_secret, "secret_type": secret_type},
        )
        iv, ciphertext = result.first().decrypted_data.split("::::")
    cipher = Cipher(
        algorithms.AES(bytes.fromhex(settings.aes_secret)),
        modes.CBC(bytes.fromhex(iv)),
        backend=default_backend(),
    )
    unpadder = padding.PKCS7(128).unpadder()
    decryptor = cipher.decryptor()
    decrypted_data = decryptor.update(bytes.fromhex(ciphertext)) + decryptor.finalize()
    unpadded_data = unpadder.update(decrypted_data) + unpadder.finalize()
    return unpadded_data.decode("utf-8")


async def rate_limit(rate_key, limit, window, incr_by: int = 1) -> bool:
    """
    Arbitrary keyed rate limits.
    """
    now = int(time.time())
    suffix = now - (now % window)
    cache_key = ("squad:rate:" + hashlib.md5(f"{rate_key}:{suffix}".encode()).hexdigest()).encode()
    try:
        count = 0
        if incr_by:
            try:
                count = await settings.memcache.incr(cache_key, incr_by)
            except Exception:
                count = None
            if count is None:
                await settings.memcache.set(cache_key, str(incr_by).encode())
                count = incr_by
        else:
            value = await settings.memcache.get(cache_key)
            if value:
                try:
                    count = int(value)
                except ValueError:
                    count = 0
        if count > limit:
            logger.warning(f"Rate limiting: {rate_key}: {count=} per {window} seconds")
            return True
        return False
    except Exception as exc:
        logger.error(f"Failed performing rate limit checks: {exc}")
    return False


async def contains_hate_speech(texts: list[str]):
    """
    Check if text has hate speach.
    """
    try:
        async with HATE_SM.get_session() as session:
            async with session.post(
                "/predict", headers={"Authorzation": get_chutes_token()}, json={"texts": texts}
            ) as resp:
                result = await resp.json()
                for idx in range(len(result)):
                    item = result[idx]
                    if item.get("label") == "hate speech":
                        logger.warning(f"Detected hate speech: {item} -> {texts[idx]}")
                        return True
                logger.info(f"No hate speech detected: {result}")
    except Exception as exc:
        logger.warning(f"Error checking hate speech content: {exc}")
    return False


async def rerank(query, texts: list[str], top_n: int = 3, auth: str = None):
    """
    Rerank the input documents based on the query to return only top_n results.
    """
    if not texts or len(texts) <= top_n:
        return texts
    rerank_docs = []
    for item in texts:
        tokens = TOKENIZER.encode(item)
        if len(tokens) > 475:
            tokens = tokens[:475]
            rerank_docs.append(TOKENIZER.decode(tokens, skip_special_tokens=True))
        else:
            rerank_docs.append(item)
    # Rerank.
    try:
        if not auth:
            auth = get_chutes_token()
        result = requests.post(
            "https://chutes-baai-bge-reranker-large.chutes.ai/rerank",
            json=dict(
                query=query,
                texts=rerank_docs,
            ),
            headers={"Authorization": auth},
        )
        ranks = result.json()
        result.raise_for_status()
        return "\n---\n".join([texts[ranks[idx]["index"]] for idx in range(min(top_n, len(ranks)))])
    except Exception as exc:
        logger.warning(f"Error running rerank: {exc}\n{traceback.format_exc()}")
    return texts[:top_n]


async def contains_nsfw(media):
    """
    Check if the target media has NSFW content.
    """
    logger.warning("TODO: nsfw")
    return False


def now_str():
    return datetime.datetime.now().isoformat()
