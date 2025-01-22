import secrets
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from sqlalchemy import text
from squad.database import get_session
from squad.config import settings


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


async def main():
    print(await decrypt(await encrypt("testing")))


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
