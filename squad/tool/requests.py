from pydantic import BaseModel, Field


class CustomToolArgs(BaseModel):
    name: str = Field(
        regex="^[a-z][a-z0-9_]*$",
        description="Function name, which must be python snake_case format",
    )
    description: str = Field(
        description="Human readable description of the function, i.e. for letting others know quickly what the function's purpose is.",
    )
    code: str
    public: bool = True
