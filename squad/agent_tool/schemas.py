"""
Association table to map agents to their tools.
"""

from sqlalchemy import (
    Column,
    String,
    ForeignKey,
    Table,
    UniqueConstraint,
)
from squad.database import Base

agent_tools = Table(
    "agent_tools",
    Base.metadata,
    Column("agent_id", String, ForeignKey("agents.agent_id", ondelete="CASCADE")),
    Column("tool_id", String, ForeignKey("tools.tool_id", ondelete="CASCADE")),
    UniqueConstraint("agent_id", "tool_id", name="uq_agent_tool"),
)
