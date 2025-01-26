"""
ORM for invocations.
"""

import base64
import secrets
from sqlalchemy import (
    select,
    Column,
    String,
    DateTime,
    ForeignKey,
    Boolean,
)
from squad.database import Base, get_session


class Invocation(Base):
    __tablename__ = "invocations"
    invocation_id = Column(String, primary_key=True)
    agent_id = Column(String, ForeignKey("agents.agent_id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True))
    source = Column(String, nullable=False, default="api")
    task = Column(String, nullable=False)
    public = Column(Boolean, default=True)
    status = Column(String, nullable=True, default="pending")


async def get_invocation(session, _id):
    """
    Load an invocation by ID.
    """
    return (
        (await session.execute(select(Invocation).where(Invocation.invocation_id == _id)))
        .unique()
        .scalar_one_or_none()
    )


async def get_unique_id(length: int = 8):
    """
    Unique ID generator.
    """

    def _gen_candidate():
        num_bytes = (length * 6) // 8 + 1
        random_bytes = secrets.token_bytes(num_bytes)
        return base64.urlsafe_b64encode(random_bytes).decode("utf-8")[:length]

    async with get_session() as session:
        candidate = await _gen_candidate()
        while get_invocation(session, candidate):
            candidate = await _gen_candidate()
        return candidate
