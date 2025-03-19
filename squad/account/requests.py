"""
Account-related pydantic models.
"""

from pydantic import BaseModel, Field
from squad.config import settings
from squad.agent_config import settings as agent_settings


class AccountLimitRequest(BaseModel):
    user_id: str
    max_steps: int = Field(settings.default_limit_max_steps, ge=1, le=1000)
    max_execution_time: int = Field(
        settings.default_limit_max_execution_time, ge=30, le=24 * 60 * 60
    )
    max_invocations_window: int = Field(
        settings.default_limit_max_invocations_window, ge=30, le=24 * 60 * 60
    )
    max_invocations: int = Field(settings.default_limit_max_invocations, ge=1)
    max_tools: int = Field(settings.default_limit_max_tools, ge=1)
    max_agents: int = Field(settings.default_limit_max_agents, ge=1)
    max_agent_tools: int = Field(settings.default_limit_agent_tools, ge=1)
    allowed_models: list[str] = Field([agent_settings.default_text_gen_model])
