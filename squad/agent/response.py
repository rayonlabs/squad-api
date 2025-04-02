"""
Response class for agents, to hide some sensitive details.
"""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from squad.tool.response import ToolResponse


class AgentResponse(BaseModel):
    agent_id: str
    name: str
    user_id: str
    readme: Optional[str]
    tagline: Optional[str]
    model: Optional[str]
    default_max_steps: Optional[int]
    public: Optional[bool]
    include_trace: Optional[bool]
    sys_base_prompt: Optional[str]
    sys_x_prompt: Optional[str]
    sys_api_prompt: Optional[str]
    sys_schedule_prompt: Optional[str]
    x_user_id: Optional[str]
    x_username: Optional[str]
    x_last_mentioned_at: Optional[datetime]
    x_searches: Optional[list[str]]
    x_invoke_filter: Optional[str] = None
    x_connected: Optional[bool] = False
    created_at: datetime
    updated_at: datetime
    tools: Optional[list[ToolResponse]]
    logo_id: Optional[str]

    class Config:
        from_attributes = True

    @property
    def logo(self):
        if self.logo_id:
            return f"https://logos.chutes.ai/logo/{self.logo_id}.webp"
        return None
