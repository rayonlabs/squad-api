"""
Account-related ORMs.
"""

from sqlalchemy import (
    Column,
    String,
    Integer,
)
from squad.config import settings
from squad.database import Base
from sqlalchemy.dialects.postgresql import ARRAY


class AccountLimit(Base):
    __tablename__ = "account_limits"
    user_id = Column(String, primary_key=True)
    max_steps = Column(Integer, nullable=False, default=settings.default_limit_max_steps)
    max_execution_time = Column(
        Integer, nullable=False, default=settings.default_limit_max_execution_time
    )
    max_invocations_window = Column(
        Integer, nullable=False, default=settings.default_limit_max_invocations_window
    )
    max_invocations = Column(
        Integer, nullable=False, default=settings.default_limit_max_invocations
    )
    max_tools = Column(Integer, nullable=False, default=settings.default_limit_max_tools)
    max_agents = Column(Integer, nullable=False, default=settings.default_limit_max_agents)
    max_agent_tools = Column(Integer, nullable=False, default=settings.default_limit_agent_tools)
    allowed_models = Column(ARRAY(String), nullable=False)
