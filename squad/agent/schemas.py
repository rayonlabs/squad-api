"""
ORM definitions/methods for agents.
"""

import re
from async_lru import alru_cache
from fastapi import HTTPException, status
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy import (
    select,
    Column,
    String,
    Integer,
    Boolean,
    BigInteger,
    DateTime,
)
from sqlalchemy.orm import validates
from sqlalchemy.dialects.postgresql import ARRAY
from squad.config import settings
import squad.tool.builtin as builtin
from smolagents import Tool as STool
from squad.database import Base, generate_uuid, get_session
from squad.agent_tool.schemas import agent_tools
from squad.agent.templates import DEFAULT_IMPORTS, MAIN_TEMPLATE


class Agent(Base):
    __tablename__ = "agents"
    agent_id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    readme = Column(String, nullable=True)
    tagline = Column(String, nullable=False)
    model = Column(String, nullable=False)
    user_id = Column(String, nullable=True)
    default_max_steps = Column(Integer, nullable=False, default=settings.default_max_steps)
    public = Column(Boolean, default=True)
    include_trace = Column(Boolean, default=True)

    # System prompt overrides.
    sys_base_prompt = Column(String, nullable=True)
    sys_x_prompt = Column(String, nullable=True)
    sys_api_prompt = Column(String, nullable=True)
    sys_schedule_prompt = Column(String, nullable=True)

    # X stuff.
    x_user_id = Column(String, nullable=True)
    x_username = Column(String, nullable=True)
    x_access_token = Column(String, nullable=True)
    x_refresh_token = Column(String, nullable=True)
    x_token_expires_at = Column(BigInteger, nullable=True)
    x_last_mentioned_at = Column(DateTime(timezone=True), server_default=func.now())

    # Usernames and keywords to regular search for.
    x_searches = Column(ARRAY(String), nullable=True)

    # Timestamps.
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    tools = relationship("Tool", secondary=agent_tools, back_populates="agents", lazy="joined")

    @validates("name")
    def validate_name(self, _, name):
        if not re.match(r"^[a-zA-Z0-9\._\-]{3,64}$", name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid name, please use alphanumeric, dot, dash or underscore, between 3 and 64 characters.",
            )
        return name

    @validates("tagline")
    def validate_tagline(self, _, tagline):
        if not tagline or not 3 <= len(tagline) <= 1024:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid tagline, please describe the agent with between 3 and 1024 characters!",
            )
        return tagline

    def as_executable(self, task: str, max_steps: int = None, source: str = "api"):
        """
        Get the agent as an executable python script for a given task.
        """
        config_map = {
            "sys_base_prompt": self.sys_base_prompt,
            "sys_x_prompt": self.sys_x_prompt,
            "sys_api_prompt": self.sys_api_prompt,
            "sys_schedule_prompt": self.sys_schedule_prompt,
            "agent_model": self.model,
            "agent_callbacks": [],
            "task": task,
        }
        if max_steps:
            config_map["max_steps"] = max_steps
        elif self.default_max_steps:
            config_map["max_steps"] = self.default_max_steps
        else:
            config_map["max_steps"] = settings.default_max_steps
        code = []
        imports = [DEFAULT_IMPORTS.strip()]
        tool_names = []
        for tool in self.tools:
            if tool.code:
                # This is ugly but it works just fine.
                code.append(tool.code)
                class_match = re.search(r"^class\s*(\w+)\(Tool\)", tool.code)
                class_name = None
                if class_match:
                    class_name = class_match.group(1)
                else:
                    for line in tool.code.splitlines():
                        if line.startswith("class ") and "(Tool)" in line:
                            class_name = line.split("(Tool)")[0].split(" ")[-1]
                code.append(f"{tool.name} = {class_name}()")
            else:
                ref = getattr(builtin, tool.template)
                if (
                    source not in ("X", "schedule")
                    and tool.template.startswith("X")
                    and tool.template != "XSearcher"
                ):
                    # No X actions for regular API invocations.
                    continue
                imports.append(f"from squad.tool.builtin import {tool.template}")
                if tool.template == "DangerousDynamo":
                    imports.append(
                        "from squad.tool.builtin.dangerzone import wipe_tool_creation_step"
                    )
                    config_map["agent_callbacks"].append("wipe_tool_creation_step")
                if isinstance(ref, type) and issubclass(ref, STool):
                    code.append(f"{tool.name} = {tool.template}()")
                else:
                    config_map[tool.name] = tool.tool_args
                    code.append(f"{tool.name} = {tool.template}(**__tool_args['{tool.name}'])()")
                    tool_names.append(tool.name)

        code.append(MAIN_TEMPLATE.format(tool_name_str=", ".join(tool_names)))
        final_code = "\n".join(
            [
                "\n".join(imports),
                'with open("configmap.json") as infile:\n    __tool_args = json.load(infile)',
                "\n".join(code),
            ]
        )
        return config_map, final_code


@alru_cache(maxsize=1000)
async def get_by_x(x_username: str | int, runtime: float = 0.0):
    """
    Load an agent by X username.
    """
    query = select(Agent).where(Agent.x_username.ilike(x_username))
    async with get_session() as session:
        return (await session.execute(query)).unique().scalar_one_or_none()
