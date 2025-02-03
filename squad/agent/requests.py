"""
Schema for agent args.
"""

from typing import Optional
from pydantic import BaseModel, Field
from squad.config import settings as core_settings
from squad.agent_config import settings


class AgentArgs(BaseModel):
    name: Optional[str] = Field(
        None,
        pattern=r"^[\$\w\u0080-\uFFFF\._-]{1,24}$",
        description="Agent name",
    )
    readme: Optional[str] = Field(
        None,
        description="Markdown readme to provide additional information, context, usage tips, etc.",
    )
    tagline: Optional[str] = Field(
        None,
        description="Very brief description of your agent, a TL;DR",
        min_length=1,
        max_length=1024,
    )
    model: Optional[str] = Field(
        settings.default_text_gen_model,
        description="The primary LLM to power the agent with.",
    )
    context_size: Optional[int] = Field(
        core_settings.default_context_size,
        description="Maximum context size (in tokens) of the primary agent LLM.",
    )
    default_max_steps: Optional[int] = Field(
        core_settings.default_max_steps,
        description="Default maximum number of steps before your agent terminates.",
        ge=1,
        le=50,
    )
    sys_base_prompt: Optional[str] = Field(
        None,
        description="Prompt override for the core system/coding agent prompt. Modify with great care.",
        max_length=32000,
    )
    sys_x_prompt: Optional[str] = Field(
        None,
        description="Prompt addendum for X specific tasks, meaning the agent was triggered from being mentioned in a tweet.",
        max_length=32000,
    )
    sys_api_prompt: Optional[str] = Field(
        None,
        description="Prompt addendum when handling requests from direct API/UI calls, meaning NOT triggered by X/schedule.",
    )
    sys_schedule_prompt: Optional[str] = Field(
        None,
        description="Prompt addendum when handling scheduled events, e.g. randomly tweeting N times per day.",
    )
    x_user_id: Optional[int] = Field(
        None,
        description="Numeric ID of your agent's X account. Probably best to just use username if you don't know.",
    )
    x_username: Optional[str] = Field(
        None,
        pattern=r"^[\w\u0080-\uFFFF]{1,24}$",
        description="X handle if you wish to enable X/twitter interactions for your bot. Requires X login to authorize.",
    )
    x_searches: Optional[list[str]] = Field(
        default=[],
        min_length=1,
        max_length=500,
        min_items=0,
        max_items=3,
        description='Up to 3 search strings to use to periodically search X with, can be be complex boolean operators too such as "@fooaccountname OR #bittensor -$btc"',
    )
    x_invoke_filter: Optional[str] = Field(
        None,
        max_length=50,
        description="Only trigger the agent when this literal exact string is found in X posts/tweets",
    )
    tool_ids: Optional[list[str]] = Field(
        default=None,
        description="List of tool IDs to enable for the agent.",
    )
