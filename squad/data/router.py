"""
Router to handle storage related tools (X search, brave search, memories CRD).
"""

from pydantic import ValidationError
from fastapi import APIRouter, Depends, Header, HTTPException, status
from squad.auth import User, get_current_user
from squad.config import settings
from squad.data.schemas import (
    BraveSearchParams,
    XSearchParams,
    MemorySearchParams,
    MemoryArgs,
)
from squad.storage.x import search as x_search
from squad.storage.memory import Memory
from squad.storage.memory import search as memory_search
from squad.storage.memory import delete as delete_memory
from squad.storage.memory import index_memories

router = APIRouter()


@router.post("/brave/search")
async def perform_brave_search(
    search: BraveSearchParams,
    current_user: User = Depends(get_current_user()),
):
    params = search.dict()
    async with settings.brave_sm.get_session() as session:
        async with session.get("/res/v1/web/search", params=params) as resp:
            return await resp.json()


@router.post("/x/search")
async def perform_x_search(
    search: XSearchParams,
    authorization: str = Header(None, alias="Authorization"),
    current_user: User = Depends(get_current_user()),
):
    params = search.dict()
    params["api_key"] = authorization
    _, results = await x_search(**params)
    return [doc["_source"] for doc in results["hits"]["hits"]]


@router.post("/memory/search")
async def perform_memory_search(
    search: MemorySearchParams,
    agent_id: str = Header(None, alias="X-Agent-ID"),
    authorization: str = Header(None, alias="Authorization"),
    current_user: User = Depends(get_current_user()),
):
    params = search.dict()
    params["agent_id"] = agent_id
    params["api_key"] = authorization
    _, raw = await memory_search(**params)
    return [doc["_source"] for doc in raw["hits"]["hits"]]


@router.post("/memories")
async def create_memory(
    memory_args: MemoryArgs,
    agent_id: str = Header(None, alias="X-Agent-ID"),
    authorization: str = Header(None, alias="Authorization"),
    current_user: User = Depends(get_current_user()),
):
    try:
        memory = Memory(agent_id=agent_id, **memory_args.model_dump())
    except ValidationError as exc:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    await index_memories([memory], authorization)
    return {"memory_id": memory.uid}


@router.delete("/memories/{memory_id}")
async def del_memory(
    memory_id: str,
    agent_id: str = Header(None, alias="X-Agent-ID"),
    _: User = Depends(get_current_user()),
):
    return await delete_memory(agent_id, memory_id)
