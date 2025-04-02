"""
Response class for tools.
"""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class ToolResponse(BaseModel):
    tool_id: str
    name: str
    description: str
    template: Optional[str]
    code: Optional[str]
    tool_args: Optional[dict]
    user_id: Optional[str]
    public: bool
    created_at: datetime
    logo_id: Optional[str]

    class Config:
        from_attributes = True

    @property
    def logo(self):
        if self.logo_id:
            return f"https://logos.chutes.ai/logo/{self.logo_id}.webp"
        return None
