"""
Router to handle storage related tools (X, brave, memories).
"""

from fastapi import APIRouter, Depends, Header
from squad.auth import User, get_current_user
from squad.config import settings
from squad.data.schemas import BraveSearchParams, XSearchParams, MemorySearchParams, MemoryArgs
from squad.storage.x import search as x_search
from squad.storage.memory import Memory
from squad.storage.memory import search as memory_search

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
    return await memory_search(**params)


@router.post("/memories")
async def create_memory(
    memory_args: MemoryArgs,
    agent_id: str = Header(None, alias="X-Agent-ID"),
    authorization: str = Header(None, alias="Authorization"),
    current_user: User = Depends(get_current_user()),
):
    memory = Memory(agent_id=agent_id, **memory_args.model_dump())
    return await {"memory_id": memory.uid}
