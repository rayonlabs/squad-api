"""
Request classes for secrets.
"""

from typing import List
from pydantic import BaseModel, constr, Field


class BYOKSecretArgs(BaseModel):
    name: str
    description: str
    header_key: str
    public: bool
    url_patterns: List[constr(min_length=8, max_length=128)] = Field(
        min_items=1,
        max_items=10,
        description="List of URL patterns to allow using this secret with (simple wildcard matching)",
    )


class BYOKSecretItemArgs(BaseModel):
    value: str
