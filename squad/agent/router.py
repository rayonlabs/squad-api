"""
Router to handle agents.
"""

import io
from datetime import datetime, timedelta
import orjson as json
import pybase64 as base64
from typing import Optional, Any, Annotated
from sqlalchemy import select, or_, func, exists
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status, Request, UploadFile, File
from squad.util import now_str
from squad.auth import get_current_user
from squad.config import settings
from squad.database import get_db_session
from squad.pagination import PaginatedResponse
from squad.agent.schemas import Agent, is_valid_name
from squad.agent.requests import AgentArgs
from squad.agent.response import AgentResponse
from squad.tool.schemas import Tool
from squad.invocation.schemas import Invocation, get_unique_id
from squad.storage.x import get_users, get_users_by_id

router = APIRouter()


class PaginatedAgents(PaginatedResponse):
    items: list[AgentResponse]


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


async def populate_x_account(db, agent):
    if agent.x_user_id and agent.x_username:
        return
    if agent.x_user_id:
        users = await get_users_by_id([agent.x_user_id])
        if not users or not users.get(agent.x_user_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not load X user account with user_id={agent.x_user_id}",
            )
        agent.x_username = users[agent.x_user_id]["username"]
    elif agent.x_username:
        users = await get_users([agent.x_username])
        if not users or not users.get(agent.x_username):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not load X user account with username={agent.x_username}",
            )
        agent.x_user_id = str(users[agent.x_username]["id"])


@router.get("", response_model=PaginatedAgents)
async def list_agents(
    db: AsyncSession = Depends(get_db_session),
    include_public: Optional[bool] = False,
    search: Optional[str] = None,
    limit: Optional[int] = 10,
    page: Optional[int] = 0,
    user: Any = Depends(get_current_user(raise_not_found=False)),
):
    user_id = user.user_id if user else None
    query = select(Agent)
    if search:
        query = query.where(Agent.name.ilike(f"%{search}%"))
    if include_public:
        if user:
            query = query.where(or_(Agent.user_id == user_id, Agent.public.is_(True)))
        else:
            query = query.where(Agent.public.is_(True))
    elif not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You must authenticate to see your own private agents.",
        )
    else:
        query = query.where(Agent.user_id == user_id)

    # Perform a count.
    total_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(total_query)
    total = total_result.scalar() or 0

    # Pagination.
    query = (
        query.order_by(Agent.created_at.desc())
        .offset((page or 0) * (limit or 10))
        .limit((limit or 10))
    )
    agents = (await db.execute(query)).unique().scalars().all()
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "items": [AgentResponse.from_orm(item) for item in agents],
    }


@router.get("/name_check")
async def check_agent_name(
    name: str,
    db: AsyncSession = Depends(get_db_session),
):
    if not is_valid_name(name):
        return {"valid": False, "available": False}
    query = select(exists().where(Agent.name.ilike(name)))
    agent_exists = await db.scalar(query)
    if agent_exists:
        return {"available": False, "valid": True}
    return {"available": True, "valid": True}


@router.get("/{agent_id_or_name}", response_model=AgentResponse)
async def get_agent(
    agent_id_or_name: str,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user(raise_not_found=False)),
):
    user_id = user.user_id if user else None
    if (agent := await _load_agent(db, agent_id_or_name, user_id)) is None:
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
    count = (
        await db.execute(
            select(func.count()).select_from(Agent).where(Agent.user_id == user.user_id)
        )
    ).scalar_one()
    if count >= user.limits.max_agents:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"You have reached or exceeded the maximum number of agents for your account tier: {count}",
        )
    if args.default_max_steps >= user.limits.max_steps:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Your account is limited to {user.limits.max_steps} max steps per agent.",
        )
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

    # Check the model.
    if "ANY" not in user.limits.allowed_models and agent.model not in user.limits.allowed_models:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Your account is limited to the following models: {user.limits.allowed_models}",
        )

    # Check name uniqueness.
    query = select(exists().where(Agent.name.ilike(agent.name)))
    agent_exists = await db.scalar(query)
    if agent_exists:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An agent with name {agent.name} already exists",
        )

    # Add the tools.
    if tool_ids:
        agent.tools = await _load_tools(db, tool_ids, user.user_id)
        if len(agent.tools) > user.limits.max_agent_tools:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Your account is limited to {user.limits.max_agent_tools} max tools per agent.",
            )
    await populate_x_account(db, agent)
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
    await populate_x_account(db, agent)
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


@router.post("/{agent_id_or_name}/invoke", status_code=status.HTTP_202_ACCEPTED)
async def invoke_agent(
    agent_id_or_name: str,
    request: Request,
    files: Annotated[list[UploadFile], File(max_length=10)] = None,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    task = None
    files_b64 = None
    public = None
    if request.headers.get("content-type") == "application/json":
        body = await request.json()
        task = body.get("task")
        files_b64 = body.get("files_b64")
        public = body.get("public")
    else:
        form = await request.form()
        task = form.get("task")
        public = form.get("public")
    public = str(public).lower() not in ("false", "0")
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide a task!",
        )

    invocation_id = await get_unique_id()
    agent = await _load_agent(db, agent_id_or_name, user.user_id)

    # Rate limits.
    count = (
        await db.execute(
            select(func.count())
            .select_from(Invocation)
            .where(
                Invocation.agent_id == agent.agent_id,
                Invocation.created_at
                >= func.now() - timedelta(seconds=user.limits.max_invocations_window),
            )
        )
    ).scalar_one()
    if count >= user.limits.max_invocations:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Your agent has exceeded the rate limit for the current tier: {user.limits.max_invocations} per {user.limits.max_invocations_window} seconds",
        )

    # Upload all input files to the storage bucket.
    input_paths = []
    now = datetime.now()
    base_path = f"invocations/{now.year}/{now.month}/{now.day}/{invocation_id}/inputs/"
    async with settings.s3_client() as s3:
        # Form data file uploads.
        if files:
            for file in files:
                content = await file.read()
                upload_path = f"{base_path}{file.filename}"
                input_paths.append(upload_path)
                await s3.upload_fileobj(
                    io.BytesIO(content),
                    settings.storage_bucket,
                    upload_path,
                )

        # Base64 encoded files from JSON post.
        if files_b64:
            for filename, b64_data in files_b64.items():
                content = base64.b64decode(b64_data)
                upload_path = f"{base_path}{filename}"
                input_paths.append(upload_path)
                await s3.upload_fileobj(
                    io.BytesIO(content),
                    settings.storage_bucket,
                    upload_path,
                )

    # Create the invocation.
    invocation = Invocation(
        invocation_id=invocation_id,
        agent_id=agent.agent_id,
        user_id=user.user_id,
        task=task,
        inputs=input_paths,
        public=public,
    )
    db.add(invocation)
    await db.commit()
    await settings.redis_client.xadd(
        invocation.stream_key,
        {"data": json.dumps({"log": "Queued agent call.", "timestamp": now_str()}).decode()},
    )
    return {"invocation_id": invocation_id}
