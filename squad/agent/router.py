"""
Router to handle agents.
"""

import io
import pybase64 as base64
from typing import Optional, Any, Annotated
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status, Request, UploadFile, File
from squad.auth import get_current_user
from squad.config import settings
from squad.database import get_db_session
from squad.agent.schemas import Agent
from squad.agent.requests import AgentArgs
from squad.agent.response import AgentResponse
from squad.tool.schemas import Tool
from squad.invocation.schemas import Invocation, get_unique_id
from squad.storage.x import get_users, get_users_by_id

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
    if request.headers.get("content-type") == "application/json":
        body = await request.json()
        task = body.get("task")
        files_b64 = body.get("files_b64")
    else:
        form = await request.form()
        task = form.get("task")

    print(f"GOT THIS: {agent_id_or_name=} {files=} {task=}")
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide a task!",
        )

    invocation_id = await get_unique_id()
    agent = await _load_agent(db, agent_id_or_name, user.user_id)

    # Upload all input files to the storage bucket and generate
    # presigned URLs to avoid the need for auth in the agent iso pod.
    presigned_urls = []
    input_paths = []
    async with settings.s3_client() as s3:
        # Form data file uploads.
        if files:
            for file in files:
                content = await file.read()
                upload_path = f"invocations/{invocation_id}/{file.filename}"
                input_paths.append(upload_path)
                await s3.upload_fileobj(
                    io.BytesIO(content),
                    settings.storage_bucket,
                    upload_path,
                )
                presigned_url = await s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": settings.storage_bucket, "Key": upload_path},
                    ExpiresIn=3600,
                )
                presigned_urls.append(presigned_url)

        # Base64 encoded files from JSON post.
        if files_b64:
            for filename, b64_data in files_b64.items():
                content = base64.b64decode(b64_data)
                upload_path = f"invocations/{invocation_id}/{filename}"
                input_paths.append(upload_path)
                await s3.upload_fileobj(
                    io.BytesIO(content),
                    settings.storage_bucket,
                    upload_path,
                )
                presigned_url = await s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": settings.storage_bucket, "Key": upload_path},
                    ExpiresIn=3600,
                )
                presigned_urls.append(presigned_url)

    # Create the invocation.
    invocation = Invocation(
        invocation_id=invocation_id,
        agent_id=agent.agent_id,
        user_id=user.user_id,
        task=task,
        inputs=input_paths,
        inputs_signed=presigned_urls,
    )
    db.add(invocation)
    await db.commit()
    return {"invocation_id": invocation_id}
