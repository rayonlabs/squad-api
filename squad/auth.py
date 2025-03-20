"""
Authentication stuff.
"""

import jwt
import json
import uuid
from sqlalchemy import select
from types import SimpleNamespace
from fastapi import Request, HTTPException, status, Header
from datetime import datetime, timedelta
from squad.account.schemas import AccountLimit
from squad.database import get_session
from squad.config import settings


def generate_auth_token(user_id, duration_minutes=30, issuer: str = "squad", **extra_payload):
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
                "iss": issuer,
                "sub": user_id,
            },
            **extra_payload,
        },
        key=settings.jwt_private,
        algorithm="RS256",
    )


async def get_limits(user_id: str) -> AccountLimit:
    """
    Ensure the account limits are configured for the user.
    """
    async with get_session() as session:
        limits = (
            (await session.execute(select(AccountLimit).where(AccountLimit.user_id == user_id)))
            .unique()
            .scalar_one_or_none()
        )
        if limits:
            return limits
        limits = AccountLimit(user_id=user_id)
        session.add(limits)
        await session.commit()
        await session.refresh(limits)
        return limits


async def load_chute_user(authorization: str):
    """
    Use the chutes /users/me endpoint to get the user information.
    """
    cache_key = "auth:user:" + str(uuid.uuid5(uuid.NAMESPACE_OID, authorization))
    cached = await settings.redis_client.get(cache_key)
    if cached:
        result = SimpleNamespace(**json.loads(cached.decode()))
        result.limits = await get_limits(result.user_id)
        return result

    async with settings.chutes_sm.get_session() as session:
        headers = {
            "Authorization": authorization,
        }
        async with session.get("/users/me", headers=headers, timeout=5.0) as response:
            user_data = await response.json()
            await settings.redis_client.set(cache_key, await response.text(), ex=600)
            result = SimpleNamespace(**user_data)
            result.limits = await get_limits(result.user_id)
            return result


def get_current_user(
    raise_not_found: bool = True,
):
    async def _authenticate(
        request: Request,
        authorization: str | None = Header(None, alias="Authorization"),
    ):
        try:
            return await load_chute_user(authorization)
        except Exception as exc:
            if raise_not_found:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Unauthorized: {exc}",
                )
        return None

    return _authenticate


def get_current_agent(issuer: str = "squad", scopes: list[str] = None):
    from squad.agent.schemas import Agent

    async def _authenticate(
        request: Request,
        authorization: str | None = Header(None, alias="Authorization"),
    ):
        """
        Helper to authenticate requests for agents.
        """
        token = authorization.split(" ")[-1]
        payload = jwt.decode(token, options={"verify_signature": False})
        agent_id = payload.get("agent_id")
        if not agent_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token.",
            )
        try:
            payload = jwt.decode(
                token,
                settings.jwt_public,
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
            if scopes:
                if set(payload.get("scopes", [])) & set(scopes) != set(scopes):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=f"Missing one or more required scopes, required: {scopes}",
                    )
            async with get_session() as session:
                agent = (
                    (await session.execute(select(Agent).where(Agent.agent_id == agent_id)))
                    .unique()
                    .scalar_one_or_none()
                )
                if agent:
                    return agent
        except jwt.InvalidTokenError:
            ...
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
        )

    return _authenticate
