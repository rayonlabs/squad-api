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

    class Config:
        from_attributes = True
