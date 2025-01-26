"""
Router to handle tools.
"""

from typing import Optional, Any
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status
from squad.auth import get_current_user
from squad.database import get_db_session
from squad.tool.schemas import Tool
from squad.tool.requests import ToolArgs
from squad.tool.validation import ToolValidator

router = APIRouter()


async def _load_tool(db, tool_id, user_id):
    query = select(Tool).where(Tool.tool_id == tool_id)
    if user_id:
        query = query.where(or_(Tool.user_id == user_id, Tool.public.is_(True)))
    else:
        query = query.where(Tool.public.is_(True))
    return (await db.execute(query)).unique().scalar_one_or_none()


@router.get("")
async def list_tools(
    db: AsyncSession = Depends(get_db_session),
    include_public: Optional[bool] = False,
    search: Optional[str] = None,
    user: Any = Depends(get_current_user(raise_not_found=False)),
):
    user_id = user.user_id if user else None
    query = select(Tool)
    if search:
        query = query.where(Tool.name.ilike(f"%{search}%"))
    if include_public:
        if user:
            query = query.where(or_(Tool.user_id == user_id, Tool.public.is_(True)))
        else:
            query = query.where(Tool.public.is_(True))
    elif not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You must authenticate to see your own private tools.",
        )
    else:
        query = query.where(Tool.user_id == user_id)
    return (await db.execute(query)).unique().scalars().all()


@router.get("/{tool_id}")
async def get_tool(
    tool_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user(raise_not_found=False)),
):
    user_id = user.user_id if user else None
    if (tool := await _load_tool(db, tool_id, user_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool {tool_id} not found, or is not public",
        )
    return tool


@router.post("")
async def create_tool(
    args: ToolArgs,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    args.tool_args["tool_name"] = args.name
    if not args.tool_args.get("tool_description"):
        args.tool_args["tool_description"] = args.description
    validator = ToolValidator(db, args, user)
    await validator.validate()
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
    user: Any = Depends(get_current_user()),
):
    tool = await _load_tool(db, tool_id, user.user_id)
    if not tool or tool.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool {tool_id} not found, or does not belong to you.",
        )
    await db.delete(tool)
    await db.commit()
    return {"deleted": True, "tool_id": tool_id}
