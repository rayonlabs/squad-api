"""
Router to handle agents.
"""

from typing import Optional, Any
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status, Request
from squad.auth import get_current_user
from squad.database import get_db_session
from squad.agent.schemas import Agent
from squad.agent.requests import AgentArgs
from squad.agent.response import AgentResponse
from squad.tool.schemas import Tool

router = APIRouter()


async def _load_agent(db, agent_id_or_name, user_id):
    query = select(Agent).where(
        or_(Agent.agent_id == agent_id_or_name, Agent.name.ilike(agent_id_or_name))
    )
    if user_id:
        query = query.where(or_(Agent.user_id == user_id, Agent.public.is_(True)))
    else:
        query = query.where(Agent.public.is_(True))
    return (await db.execute(query)).unique().scalar_one_or_none()


async def _load_tools(db, tool_ids, user_id):
    tools = []
    for tool_id in tool_ids:
        tool = (
            (
                await db.execute(
                    select(Tool).where(
                        Tool.tool_id == tool_id,
                        or_(Tool.user_id == user_id, Tool.public.is_(True)),
                    )
                )
            )
            .unique()
            .scalar_one_or_none()
        )
        if not tool:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid tool ID: {tool_id}",
            )
        tools.append(tool)
    return tools


@router.get("")
async def list_agents(
    db: AsyncSession = Depends(get_db_session),
    include_public: Optional[bool] = False,
    search: Optional[str] = None,
    user: Any = Depends(get_current_user(raise_not_found=False)),
):
    query = select(Agent)
    if search:
        query = query.where(Agent.name.ilike(f"%{search}%"))
    if include_public:
        if user:
            query = query.where(or_(Agent.user_id == user.user_id, Agent.public.is_(True)))
        else:
            query = query.where(Agent.public.is_(True))
    elif not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You must authenticate to see your own private agents.",
        )
    else:
        query = query.where(Agent.user_id == user.user_id)
    agents = (await db.execute(query)).unique().scalars().all()
    return [AgentResponse.from_orm(agent) for agent in agents]


@router.get("/{agent_id_or_name}", response_model=AgentResponse)
async def get_agent(
    agent_id_or_name: str,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user(raise_not_found=False)),
):
    if (agent := await _load_agent(db, agent_id_or_name, user.user_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id_or_name} not found, or is not public",
        )
    return agent


@router.post("", response_model=AgentResponse)
async def create_agent(
    args: AgentArgs,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    agent = None
    tool_ids = []
    try:
        agent_args = args.model_dump()
        if not agent_args.get("name"):
            raise ValueError("Must specify agent name")
        tool_ids = agent_args.pop("tool_ids", [])
        agent = Agent(**agent_args)
        agent.user_id = user.user_id
    except ValueError as exc:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    # Add the tools.
    if tool_ids:
        agent.tools = await _load_tools(db, tool_ids, user.user_id)

    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


@router.put("/{agent_id_or_name}", response_model=AgentResponse)
async def update_agent(
    agent_id_or_name: str,
    args: AgentArgs,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    agent = await _load_agent(db, agent_id_or_name, user.user_id)
    tool_ids = []
    agent_args = {}
    try:
        agent_args = args.model_dump()
        tool_ids = agent_args.pop("tool_ids", [])
        Agent(**agent_args)
    except ValueError as exc:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    body = await request.json()
    for key, value in body.items():
        if key in agent_args and key != "tool_ids":
            setattr(agent, key, value)

    # Add the tools.
    tool_ids = body.get("tool_ids")
    if tool_ids is not None:
        agent.tools = await _load_tools(db, tool_ids, user.user_id)
    await db.commit()
    await db.refresh(agent)
    return agent


@router.delete("/{agent_id_or_name}")
async def delete_agent(
    agent_id_or_name: str,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    agent = await _load_agent(db, agent_id_or_name, user.user_id)
    if not agent or agent.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id_or_name} not found, or does not belong to you.",
        )
    agent_id = agent.agent_id
    await db.delete(agent)
    await db.commit()
    return {"deleted": True, "agent_id": agent_id}
