"""
Authentication stuff.
"""

import jwt
import json
import uuid
import aiohttp
from sqlalchemy import select
from types import SimpleNamespace
from fastapi import Request, HTTPException, status, Header
from datetime import datetime, timedelta
from squad.database import get_session
from squad.config import settings
from squad.agent.schemas import Agent


def generate_auth_token(user_id, duration_minutes=30, **extra_payload):
    """
    Create a JWT on behalf of a user for chutes API interaction.
    """
    if settings.dev_auth:
        return settings.dev_auth
    iat_timestamp = datetime.utcnow()
    exp_timestamp = datetime.utcnow() + timedelta(minutes=duration_minutes)
    return jwt.encode(
        payload={
            **{
                "exp": exp_timestamp,
                "iat": iat_timestamp,
                "iss": "squad",
                "sub": user_id,
            },
            **extra_payload,
        },
        key=settings.jwt_private,
        algorithm="RS256",
    )


async def load_chute_user(authorization: str):
    """
    Use the chutes /users/me endpoint to get the user information.
    """
    cache_key = "auth:user:" + str(uuid.uuid5(uuid.NAMESPACE_OID, authorization))
    cached = await settings.redis_client.get(cache_key)
    if cached:
        return SimpleNamespace(**json.loads(cached.decode()))
    async with aiohttp.ClientSession(
        base_url="https://api.chutes.ai",
        raise_for_status=True,
        headers={
            "Authorization": authorization,
        },
    ) as session:
        async with session.get("/users/me", timeout=5.0) as response:
            user_data = await response.json()
            await settings.redis_client.set(cache_key, await response.text(), ex=600)
            return SimpleNamespace(**user_data)


def get_current_user(
    raise_not_found: bool = True,
):
    async def _authenticate(
        request: Request,
        authorization: str | None = Header(None, alias="Authorization"),
    ):
        try:
            return await load_chute_user(authorization)
        except Exception:
            if raise_not_found:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Unauthorized.",
                )
        return None

    return _authenticate


def get_current_agent(issuer: str = "squad-agent"):
    async def _authenticate(
        request: Request,
        authorization: str | None = Header(None, alias="Authorization"),
    ):
        """
        Helper to authenticate requests for agents.
        """
        token = authorization.split(" ")[-1]
        payload = jwt.decode(token, options={"verify_signature": False})
        agent_id = payload.get("sub")
        try:
            payload = jwt.decode(
                token,
                settings.squad_cert,
                algorithms=["RS256"],
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "require": ["exp", "iat", "iss"],
                },
                issuer=issuer,
            )
            async with get_session() as session:
                agent = (
                    await session.execute(select(Agent).where(Agent.agent_id == agent_id))
                ).scalar_one_or_none()
                if agent:
                    return agent
        except jwt.InvalidTokenError:
            ...
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
        )

    return _authenticate
