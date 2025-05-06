"""
Router to handle storage related tools (X search, brave search, memories CRD).
"""

import re
import jwt
import aiohttp
import pybase64 as base64
from typing import Any
from datetime import datetime
from pydantic import ValidationError
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select, func, or_, String
from squad.auth import get_current_user, get_current_agent
from squad.agent.schemas import get_by_id
from squad.secret.schemas import BYOKSecret, BYOKSecretItem
from squad.config import settings
from squad.util import decrypt
from squad.auth import generate_auth_token
from squad.database import get_session
from squad.data.schemas import (
    DataUniverseSearchParams,
    ApexWebSearchParams,
    BraveSearchParams,
    XSearchParams,
    MemorySearchParams,
    MemoryArgs,
    BYOKParams,
)
from squad.tool.schemas import Tool
from squad.storage.x import search as x_search
from squad.storage.x import Tweet
from squad.storage.memory import Memory
from squad.storage.memory import search as memory_search
from squad.storage.memory import delete as delete_memory
from squad.storage.memory import index_memories

router = APIRouter()


async def _get_agent(request: Request, agent_id: str, authorization: str, current_user: Any):
    """
    Helper to get agent and check auth.
    """
    agent = None
    if current_user:
        if not agent_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You must specify an 'X-Agent-ID' header!",
            )
        agent = await get_by_id(agent_id)
        if agent:
            if not agent.public and agent.user_id != current_user.user_id:
                agent = None
    else:
        agent_auth = await get_current_agent(issuer="squad")(request, authorization)
        agent = await get_by_id(agent_auth.agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found, or does not belong to you.",
        )
    return agent


@router.post("/brave/search")
async def perform_brave_search(
    search: BraveSearchParams,
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
):
    await get_current_agent(issuer="squad")(request, authorization)
    if not search.count:
        search.count = 5
    params = search.dict()
    params = {k: v if isinstance(v, str) else str(v) for k, v in params.items() if v is not None}
    async with settings.brave_sm.get_session() as session:
        async with session.get("/res/v1/web/search", params=params) as resp:
            return await resp.json()


@router.post("/x/search")
async def perform_x_search(
    search: XSearchParams,
    request: Request,
    authorization: str = Header(alias="Authorization"),
    current_user: Any = Depends(get_current_user(raise_not_found=False)),
) -> list[Tweet]:
    if not current_user:
        await get_current_agent(issuer="squad", scopes=["x"])(request, authorization)
    params = search.dict()
    params["api_key"] = "Bearer " + generate_auth_token(
        settings.default_user_id, duration_minutes=5
    )
    tweets, _ = await x_search(**params)
    return tweets


@router.post("/data_universe/search")
async def perform_data_universe_search(
    search: DataUniverseSearchParams,
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    current_user: Any = Depends(get_current_user(raise_not_found=False)),
):
    if not current_user:
        await get_current_agent(issuer="squad")(request, authorization)
    payload = search.model_dump()
    for key, v in payload.items():
        if isinstance(v, datetime):
            payload[key] = v.isoformat()
    async with settings.data_universe_sm.get_session() as session:
        async with session.post("/api/v1/on_demand_data_request", json=payload) as resp:
            return await resp.json()


@router.post("/apex/web_search")
async def perform_apx_web_search(
    search: ApexWebSearchParams,
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    current_user: Any = Depends(get_current_user(raise_not_found=False)),
):
    if not current_user:
        await get_current_agent(issuer="squad")(request, authorization)
    payload = search.model_dump()
    for key, v in payload.items():
        if isinstance(v, datetime):
            payload[key] = v.isoformat()
    async with settings.apex_search_sm.get_session() as session:
        async with session.post("/web_retrieval", json=payload) as resp:
            return await resp.json()


@router.post("/byok")
async def perform_byok_request(
    request_args: BYOKParams,
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
):
    await get_current_agent(issuer="squad")(request, authorization)
    payload = jwt.decode(authorization, options={"verify_signature": False})
    user_id = payload.get("sub")

    async with get_session() as db:
        byok_secret = await db.execute(
            select(BYOKSecret).where(
                BYOKSecret.name == request_args.secret_name,
                or_(
                    BYOKSecret.public.is_(True),
                    BYOKSecret.user_id == user_id,
                ),
            )
        ).scalar_one_or_none()
        if not byok_secret:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found")
        secret_item = await db.execute(
            select(BYOKSecretItem).where(
                BYOKSecretItem.secret_id == byok_secret.secret_id,
                BYOKSecretItem.user_id == user_id,
            )
        ).scalar_one_or_none()
        if not secret_item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Secret item not found"
            )

        # Ensure the URL matches the secret's allowed pattern.
        patterns = [
            pattern.replace(".", "\\.").replace("*", ".*") for pattern in byok_secret.url_patterns
        ]
        if not any([re.search(pattern, request_args.url, re.I) for pattern in patterns]):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Secret is not allowed for requested URL",
            )

        # Validate URL, method, etc.
        tool_query = select(Tool).where(
            Tool.name == request_args.tool_name,
            func.jsonb_extract_path_text(Tool.tool_args, "secret_name").cast(String)
            == request_args.secret_name,
        )
        tool = await db.execute(tool_query).scalar_one_or_none()
        if not tool:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No matching tool found for this request.",
            )

        # Generate the headers.
        header_value = await decrypt(secret_item.encrypted_value, secret_type="byok")
    try:
        kwargs = {
            "headers": {
                **request_args.headers,
                **{
                    byok_secret.header_key: header_value,
                },
            },
            "params": request_args.params,
        }
        if request_args.body:
            if request_args.body.type == "bytes":
                kwargs["body"] = base64.b64decode(request_args.body.value)
            else:
                kwargs["body"] = request_args.body.value
        async with aiohttp.ClientSession(raise_for_status=False) as session:
            async with getattr(session, request_args.method)(
                request_args.url,
                **kwargs,
            ) as resp:
                return {
                    "headers": resp.headers,
                    "body": base64.b64encode(await resp.read()).decode,
                }

    except Exception:
        return {}


@router.post("/memory/search")
async def perform_memory_search(
    search: MemorySearchParams,
    request: Request,
    agent_id: str = Header(None, alias="X-Agent-ID"),
    authorization: str = Header(alias="Authorization"),
    current_user: Any = Depends(get_current_user(raise_not_found=False)),
) -> list[Memory]:
    agent = await _get_agent(request, agent_id, authorization, current_user)
    params = search.dict()
    params["agent_id"] = agent.agent_id
    params["api_key"] = "Bearer " + generate_auth_token(
        settings.default_user_id, duration_minutes=5
    )
    memories, _ = await memory_search(**params)
    return memories


@router.post("/memories")
async def create_memory(
    memory_args: MemoryArgs,
    request: Request,
    agent_id: str = Header(None, alias="X-Agent-ID"),
    authorization: str = Header(alias="Authorization"),
    current_user: Any = Depends(get_current_user(raise_not_found=False)),
) -> Memory:
    agent = await _get_agent(request, agent_id, authorization, current_user)
    if current_user and agent.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot create memories for agents that are not yours, silly goose.",
        )
    try:
        memory = Memory(agent_id=agent.agent_id, **memory_args.model_dump())
        if not memory.language:
            memory.language = "english"
    except ValidationError as exc:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    await index_memories([memory], authorization)
    return memory


@router.delete("/memories/{memory_id}")
async def del_memory(
    memory_id: str,
    request: Request,
    agent_id: str = Header(None, alias="X-Agent-ID"),
    authorization: str = Header(alias="Authorization"),
    current_user: Any = Depends(get_current_user(raise_not_found=False)),
) -> dict:
    agent = await _get_agent(request, agent_id, authorization, current_user)
    if current_user and agent.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot delete memories for agents that are not yours, silly goose.",
        )
    return await delete_memory(agent.agent_id, memory_id)
