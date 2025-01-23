"""
Router to handle tools.
"""

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status
from squad.auth import get_current_user, User
from squad.database import get_db_session
from squad.tool.schemas import Tool
from squad.tool.requests import ToolArgs

router = APIRouter()


async def _load_tool(db, tool_id, user_id):
    return (
        (
            await db.execute(
                select(Tool).where(
                    Tool.tool_id == tool_id, or_(Tool.public.is_(True), Tool.user_id == user_id)
                )
            )
        )
        .unique()
        .scalar_one_or_none()
    )


@router.get("")
async def list_tools(
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user()),
):
    return []


@router.get("/{tool_id}")
async def get_tool(
    tool_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user()),
):
    if (tool := await _load_tool(db, tool_id, user.user_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool {tool_id} not found, or is not public",
        )
    return tool


@router.post("")
async def create_tool(
    args: ToolArgs,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user()),
):
    existing = (
        (
            await db.execute(
                select(Tool).where(Tool.name.ilike(args.name), Tool.user_id == user.user_id)
            )
        )
        .unique()
        .scalar_one_or_none()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tool with name {args.name} already exists for your user",
        )
    tool = None
    try:
        tool = Tool(**args.model_dump())
        tool.user_id = user.user_id
    except ValueError as exc:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return tool


@router.delete("/{tool_id}")
async def delete_tool(
    tool_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user()),
):
    if (tool := await _load_tool(db, tool_id, user.user_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool {tool_id} not found, or is not public",
        )
    return tool
    await db.delete(tool)
    await db.commit()
    return {"deleted": True, "tool_id": tool_id}
