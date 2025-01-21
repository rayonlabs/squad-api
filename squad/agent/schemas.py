"""
ORM definitions/methods for agents.
"""

import re
from fastapi import HTTPException, status
from sqlalchemy.sql import func
from sqlalchemy import (
    text,
    Column,
    String,
    DateTime,
    UniqueConstraint,
)
from sqlalchemy.orm import validates
from sqlalchemy.dialects.postgresql import ARRAY
from squad.database import Base, generate_uuid


class Agent(Base):
    __tablename__ = "agents"
    agent_id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String(64).with_variant(String(64).collate("NOCASE"), "postgresql"))
    readme = Column(String, nullable=True)
    tagline = Column(String, nullable=False)
    model = Column(String, nullable=False)
    user_id = Column(String, nullable=False)

    # System prompt overrides.
    system_prompt = Column(String, nullable=True)

    # X auth.
    x_user_id = Column(String, nullable=True)
    x_access_token = Column(String, nullable=True)
    x_refresh_token = Column(String, nullable=True)
    X_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Usernames and keywords to follow.
    x_follow_users = Column(ARRAY(String), nullable=True)
    x_follow_keywords = Column(ARRAY(String), nullable=True)

    # Timestamps.
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(text("LOWER(name)"), name="unique_agent_name_case_insensitive"),
    )

    @validates("name")
    def validate_name(self, _, name):
        if not re.match(r"^[a-zA-Z0-9\._\-]{3,64}$", name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid name, please use alphanumeric, dot, dash or underscore, between 3 and 64 characters.",
            )
        return name

    @validates("tagline")
    def validate_tagline(self, _, tagline):
        if not tagline or not 3 <= len(tagline) <= 1024:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid tagline, please describe the agent with between 3 and 1024 characters!",
            )
        return tagline
