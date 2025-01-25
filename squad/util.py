import uuid
import time
import secrets
import aiohttp
from loguru import logger
from contextlib import asynccontextmanager
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from sqlalchemy import text
from squad.database import get_session
from squad.config import settings
from squad.auth import generate_auth_token


@asynccontextmanager
async def chutes_get(path, user, **kwargs):
    """
    Perform GET request to chutes API as user.
    """
    async with aiohttp.ClientSession(
        base_url="https://api.chutes.ai", raise_for_status=True
    ) as session:
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
    async with aiohttp.ClientSession(
        base_url="https://api.chutes.ai", raise_for_status=True
    ) as session:
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
    cache_key = "squad:rate:" + str(uuid.uuid5(uuid.NAMESPACE_OID, rate_key))
    now = time.time()
    async with settings.redis_client.pipeline() as pipe:
        await pipe.zremrangebyscore(cache_key, 0, now - window)
        await pipe.zcard(cache_key)
        # Add incr_by timestamps spread over a tiny interval to avoid collisions
        for i in range(incr_by):
            await pipe.zadd(cache_key, {str(now + i / 1000): now + i / 1000})
        await pipe.expire(cache_key, window)
        _, count, *_ = await pipe.execute()
    if count >= limit:
        logger.warning(f"Rate limiting: {rate_key}: {count=} per {window} seconds")
        return True
    return False
