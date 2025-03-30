"""
Response classes for secrets.
"""

from pydantic import BaseModel
from datetime import datetime


class BYOKSecretResponse(BaseModel):
    secret_id: str
    name: str
    description: str
    header_key: str
    user_id: str
    public: bool
    created_at: datetime
    url_patterns: list[str]

    class Config:
        from_attributes = True
