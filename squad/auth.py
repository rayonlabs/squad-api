"""
Authentication stuff.
"""

import jwt
from datetime import datetime, timedelta
from pydantic import BaseModel
from squad.config import settings


def generate_chutes_auth_token(user, duration_minutes=30, **extra_payload):
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
                "sub": user.user_id,
            },
            **extra_payload,
        },
        key=settings.jwt_private,
        algorithm="RS256",
    )


class User(BaseModel):
    user_id: str
    username: str


def get_current_user():
    # XXX TODO
    async def _authenticate():
        return None

    return _authenticate


def get_current_agent():
    # XXX TODO
    async def _authenticate():
        return None

    return _authenticate
