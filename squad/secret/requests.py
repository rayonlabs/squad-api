"""
Request classes for secrets.
"""

from pydantic import BaseModel


class BYOKSecretArgs(BaseModel):
    name: str
    description: str
    header_key: str
    public: bool


class BYOKSecretItemArgs(BaseModel):
    value: str
