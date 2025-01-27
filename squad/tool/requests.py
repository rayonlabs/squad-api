"""
Schema for tool creation args.
"""

import types
from typing import Optional
from pydantic import BaseModel, Field
import squad.tool.builtin as builtin


class ToolArgs(BaseModel):
    name: str = Field(
        pattern="^[a-z][a-z0-9_]*$",
        description="Function name, which must be python snake_case format",
    )
    description: str = Field(
        description="Human readable description of the function, i.e. for letting others know quickly what the function's purpose is.",
    )
    code: Optional[str] = Field(
        None,
        description="Source code of the tool, if this is a custom tool",
    )
    template: Optional[str] = Field(
        None,
        enum=[
            f
            for f in dir(builtin)
            if f != "Tool"
            and not f.startswith("_")
            and not isinstance(getattr(builtin, f, None), types.ModuleType)
        ],
        description="Template, when using built-in tools",
    )
    public: Optional[bool] = Field(True, description="Allow others to use this tool as well")
    tool_args: dict = {}
