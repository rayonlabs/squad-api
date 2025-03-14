"""
ORM definitions/methods for agents.
"""

import re
from async_lru import alru_cache
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func
from sqlalchemy import (
    select,
    Column,
    String,
    Integer,
    Index,
    Boolean,
    BigInteger,
    DateTime,
)
from sqlalchemy.dialects.postgresql import ARRAY
from squad.config import settings
import squad.tool.builtin as builtin
from smolagents import Tool as STool
from squad.tool.prompts import DEFAULT_SYSTEM_PROMPT, DEFAULT_X_ADDENDUM
from squad.database import Base, generate_uuid, get_session
from squad.agent_tool.schemas import agent_tools
from squad.agent.templates import DEFAULT_IMPORTS, MAIN_TEMPLATE


def is_valid_name(name):
    if (
        not isinstance(name, str)
        or not re.match(r"^(?:([a-zA-Z0-9_\.-]+)/)*([a-z0-9][a-z0-9_\.\/-]*)$", name, re.I)
        or len(name) >= 64
    ):
        return False
    return True


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

    # Context size limits.
    context_size = Column(Integer, nullable=False, default=settings.default_context_size)

    # X stuff.
    x_user_id = Column(String, nullable=True)
    x_username = Column(String, nullable=True)
    x_access_token = Column(String, nullable=True)
    x_refresh_token = Column(String, nullable=True)
    x_token_expires_at = Column(BigInteger, nullable=True)
    x_last_mentioned_at = Column(DateTime(timezone=True), server_default=func.now())
    x_invoke_filter = Column(String, nullable=True)

    # Usernames and keywords to regular search for.
    x_searches = Column(ARRAY(String), nullable=True)

    # Timestamps.
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    tools = relationship("Tool", secondary=agent_tools, back_populates="agents", lazy="joined")
    invocations = relationship("Invocation", back_populates="agent")

    __table_args__ = (
        Index("unique_x_user", "x_username", unique=True, postgresql_where=(x_username != None)),  # noqa
        Index("unique_name", "name", unique=True),  # noqa
    )

    @validates("name")
    def validate_name(self, _, name):
        if not is_valid_name(name):
            raise ValueError(f"Invalid agent name: {name}")
        return name

    @property
    def x_connected(self):
        return self.x_token_expires_at is not None

    def as_executable(
        self, task: str, max_steps: int = None, source: str = "api", input_files: list[str] = None
    ):
        """
        Get the agent as an executable python script for a given task.
        """
        if input_files:
            task = "\n".join(
                [
                    "You have access to the following input files related to the task:",
                ]
                + input_files
                + ["\n", task]
            )
        config_map = {
            "system_prompt": DEFAULT_SYSTEM_PROMPT
            + ("\n" + self.sys_base_prompt if self.sys_base_prompt else ""),
            "agent_model": self.model,
            "context_size": self.context_size or settings.default_context_size,
            "agent_callbacks": [],
            "task": task,
            "tools": {},
        }
        if source == "x":
            config_map["system_prompt"] += "\n" + self.sys_x_prompt or DEFAULT_X_ADDENDUM.replace(
                "USERNAME", self.x_username
            )
        elif source == "api":
            if self.sys_api_prompt:
                config_map["system_prompt"] += "\n" + self.sys_api_prompt
        else:
            if self.sys_schedule_prompt:
                config_map["system_prompt"] += "\n" + self.sys_schedule_prompt
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
                    source not in ("x", "schedule")
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
                    tool_names.append(tool.name)
                else:
                    config_map["tools"][tool.name] = tool.tool_args
                    code.append(
                        f"{tool.name} = {tool.template}(**__tool_args['tools']['{tool.name}'])()"
                    )
                    tool_names.append(tool.name)

        code.append(MAIN_TEMPLATE.format(tool_name_str=", ".join(tool_names)))
        final_code = "\n".join(
            [
                "\n".join(imports),
                'with open(os.path.join(os.path.dirname(__file__), "configmap.json")) as infile:\n    __tool_args = json.load(infile)',
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


async def get_by_id(agent_id: str):
    async with get_session() as session:
        return (
            (await session.execute(select(Agent).where(Agent.agent_id == agent_id)))
            .unique()
            .scalar_one_or_none()
        )
