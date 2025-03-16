"""
Router to handle storage related tools (X search, brave search, memories CRD).
"""

from typing import Any
from pydantic import ValidationError
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from squad.auth import get_current_user, get_current_agent
from squad.agent.schemas import get_by_id
from squad.config import settings
from squad.data.schemas import (
    BraveSearchParams,
    XSearchParams,
    MemorySearchParams,
    MemoryArgs,
)
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
    params["api_key"] = authorization
    tweets, _ = await x_search(**params)
    return tweets


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
    params["api_key"] = authorization
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
