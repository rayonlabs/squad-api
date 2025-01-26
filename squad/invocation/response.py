"""
Response class for invocations.
"""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class InvocationResponse(BaseModel):
    invocation_id: str
    agent_id: str
    user_id: str
    source: str
    task: str
    public: bool
    status: str
    inputs: Optional[list[str]] = []
    outputs: Optional[list[str]] = []
    answer: Optional[dict] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True
