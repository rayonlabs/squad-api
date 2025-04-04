"""
(Minimal) validation code for tool creation.
"""

import aiohttp
from loguru import logger
from sqlalchemy import select
from typing import Optional
from pydantic import BaseModel, Field, ValidationError, constr
from fastapi import HTTPException, status
from squad.tool.schemas import Tool
from smolagents import Tool as STool
import squad.util as util
from squad.auth import generate_auth_token
import squad.tool.builtin as builtin
from squad.config import settings
from squad.database import get_session
from squad.secret.schemas import BYOKSecret
from sqlalchemy import or_


class BaseArgs(BaseModel):
    model_config = {"none_as_default": True}


class ImageArgs(BaseArgs):
    model: str
    tool_name: Optional[str] = constr(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    tool_description: Optional[str] = Field(
        None, description="Tool description provided to agent LLM"
    )
    height: int = Field(1024, ge=128, le=2048)
    width: int = Field(1024, ge=128, le=2048)
    num_inference_steps: int = Field(25, ge=1, le=50)
    guidance_scale: float = Field(3.5, ge=0.0, le=10.0)
    seed: Optional[int] = 42

    # Some models support these.
    negative_prompt: Optional[str] = Field(
        None, description="Negative prompt to use when generating"
    )
    image_b64: Optional[list[str]] = Field(
        None, description="Optional input image in base64, when supported"
    )
    img_guidance_scale: Optional[float] = Field(None, ge=1.0, le=20.0)


class LLMArgs(BaseArgs):
    model: str
    tool_name: Optional[str] = constr(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    tool_description: Optional[str] = Field(
        None, description="Tool description provided to agent LLM"
    )
    endpoint: Optional[str] = Field("chat", enum=["chat", "completion"])
    system_prompt: Optional[str] = Field(
        None, description="System prompt to use when calling the LLM"
    )
    temperature: float = Field(0.7, ge=0.0, le=3.0)
    max_tokens: Optional[int] = Field(None, ge=1, le=20000)


class TTSArgs(BaseArgs):
    voice: str
    slug: constr(pattern="^[a-z0-9-]+$") = "chutes-kokoro-82m"
    tool_name: Optional[str] = constr(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    tool_description: Optional[str] = Field(
        None, description="Tool description provided to agent LLM"
    )


class MemoryArgs(BaseArgs):
    static_session_id: Optional[str] = Field(
        None, description="Optional static session ID to segment memories"
    )
    tool_name: Optional[str] = constr(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    tool_description: Optional[str] = Field(
        None, description="Tool description provided to agent LLM"
    )


class VLMArgs(LLMArgs): ...


class AgentCallerArgs(BaseArgs):
    agent: str
    tool_description: Optional[str] = Field(
        None, description="Tool description provided to agent LLM"
    )
    tool_name: str = constr(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    public: Optional[bool] = True


class BYOKArgs(BaseArgs):
    upstream_url: str
    secret_name: str
    method: Optional[str] = constr(pattern=r"^(post|put|get|head|patch|delete)$")
    tool_name: Optional[str] = constr(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    tool_description: Optional[str] = Field(
        None, description="Tool description provided to agent LLM"
    )


class ToolValidator:
    def __init__(self, db, args, user):
        self.db = db
        self.args = args
        self.user = user

    async def _check_duplicate_name(self):
        """
        Check for tools with the same name, no duplicates per user.
        """
        existing = (
            (
                await self.db.execute(
                    select(Tool).where(
                        Tool.name.ilike(self.args.name), Tool.user_id == self.user.user_id
                    )
                )
            )
            .unique()
            .scalar_one_or_none()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tool with name {self.args.name} already exists for user {self.user.username}",
            )

    async def check_chute_exists(self, name, template):
        """
        Check if a chute exists and has the endpoint expected.
        """
        try:
            async with util.chutes_get(f"/chutes/{name}", self.user) as resp:
                chute = await resp.json()
                assert chute.get("standard_template") == template
                assert chute.get("name") == name
        except Exception as exc:
            logger.info(f"Failed to fetch chute: {self.user=} {name=} {exc}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not find chute {name} with template {template}",
            )

    async def validate_image_tool(self):
        """
        Validate image tool template args.
        """
        await self.check_chute_exists(self.args.tool_args.get("model"), "diffusion")
        try:
            ImageArgs(**self.args.tool_args)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Validation error: {exc}",
            )

    async def validate_llm_tool(self):
        """
        Validate LLM template args.
        """
        await self.check_chute_exists(self.args.tool_args.get("model"), "vllm")
        try:
            LLMArgs(**self.args.tool_args)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Validation error: {exc}",
            )

    validate_vlm_tool = validate_llm_tool

    async def validate_tts_tool(self):
        """
        Validate text-to-speech tools.
        """
        try:
            tts_args = TTSArgs(**self.args.tool_args)
            exists = False
            async with util.chutes_get(
                "/chutes/", self.user, params={"include_public": "true", "slug": tts_args.slug}
            ) as resp:
                data = await resp.json()
                for chute in data.get("items", []):
                    cords = data.get("cord_refs", {}).get(chute["cord_ref_id"])
                    for cord in cords:
                        if cord.get("path") == "/speak":
                            exists = True
                            break
                    if exists:
                        break
            if not exists:
                raise Exception("Chute not found with {tts_args.slug=}")
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Validation error: {exc}",
            )

    async def validate_agent_caller_tool(self):
        """
        Validate agent caller tools.
        """
        try:
            agent_args = AgentCallerArgs(**self.args.tool_args)
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{settings.squad_api_base_url}/agents/{agent_args.agent}",
                    headers={"Authorization": f"Bearer {generate_auth_token(self.user.user_id)}"},
                ) as resp:
                    resp.raise_for_status()
                    return
        except Exception:
            ...

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid agent caller definition: {agent_args.model_dump()}",
        )

    async def validate_byok_tool(self):
        """
        Validate "bring your own key" request tools.
        """
        try:
            byok_args = BYOKArgs(**self.args.tool_args)
            async with get_session() as session:
                secret = (
                    (
                        await session.execute(
                            select(BYOKSecret).where(
                                BYOKSecret.name == byok_args.secret_name,
                                or_(
                                    BYOKSecret.user_id == self.user.user_id,
                                    BYOKSecret.public.is_(True),
                                ),
                            )
                        )
                    )
                    .unique()
                    .scalar_one_or_none()
                )
                if not secret:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid BYOK tool: secret {byok_args.secret_name} not found",
                    )
                return
        except Exception:
            ...

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid BYOK tool defintion",
        )

    async def validate_memory_tool(self):
        """
        Validate memory tools.
        """
        try:
            MemoryArgs(**self.args.tool_args)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Validation error: {exc}",
            )

    validate_memory_searcher = validate_memory_tool
    validate_memory_creator = validate_memory_tool
    validate_memory_eraser = validate_memory_tool

    async def validate(self):
        """
        Validate the tool spec.
        """
        if not self.args.template:
            return
        if hasattr(self, f"validate_{self.args.template}"):
            await getattr(self, f"validate_{self.args.template}")()
            return
        tool = getattr(builtin, self.args.template, None)
        if not tool or not issubclass(tool, STool):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid template: {self.args.template}",
            )
