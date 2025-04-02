"""
Utilities and helpers.
"""

import os
import hashlib
import time
import secrets
import datetime
import requests
import traceback
import aiohttp
import pybase64 as base64
from fastapi import HTTPException, status
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
TOKENIZER = {}


@lru_cache(maxsize=1)
def tokenizer():
    import transformers

    if not TOKENIZER:
        TOKENIZER["t"] = transformers.AutoTokenizer.from_pretrained(
            os.path.join(os.path.dirname(__file__), "..", "bge-reranker-large")
        )
    return TOKENIZER["t"]


def get_chutes_token():
    token = generate_auth_token(settings.default_user_id, duration_minutes=5)
    return f"Bearer {token}"


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
                "/predict", headers={"Authorization": get_chutes_token()}, json={"texts": texts}
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
        tokens = tokenizer().encode(item)
        if len(tokens) > 475:
            tokens = tokens[:475]
            rerank_docs.append(tokenizer().decode(tokens, skip_special_tokens=True))
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


async def contains_nsfw(media_bytes, content_type):
    """
    Check if the target media has NSFW content.
    """
    base_enc = base64.b64encode(media_bytes).decode()
    if not content_type.startswith("image/"):
        logger.info("TODO: video NSFW check")
    try:
        async with NSFW_SM.get_session() as session:
            async with session.post(
                "/image",
                headers={"Authorization": get_chutes_token()},
                json={"image_b64": base_enc},
                timeout=10.0,
            ) as resp:
                result = await resp.json()
                if result.get("label") == "nsfw":
                    logger.warning(f"Detected NSFW content: {result}")
                    return True
                logger.info(f"No NSFW detected: {result}")
    except Exception as exc:
        logger.warning(f"Error checking NSFW content: {exc}")
    return False


def now_str():
    return datetime.datetime.now().isoformat()


async def validate_logo(logo_id: str):
    """
    Check if a logo is valid.
    """
    if not logo_id:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://logos.chutes.ai/logo/{logo_id}.webp") as resp:
                resp.raise_for_status()
                image_bytes = await resp.read()
                if await contains_nsfw(image_bytes, "image/webp"):
                    raise ValueError("Image contains NSFW!")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid logo_id: {logo_id}: {exc}",
        )
