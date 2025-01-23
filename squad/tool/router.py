"""
Router to handle tools.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status
from squad.auth import get_current_user, User
from squad.database import get_db_session
from squad.tool.schemas import Tool, CustomTool
from squad.tool.requests import CustomToolArgs

router = APIRouter()


@router.get("/")
async def list_tools(
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user()),
):
    return []


@router.post("custom/")
async def create_custom_tool(
    args: CustomToolArgs,
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
            detail=f"Custom with name {args.name} already exists for your user",
        )
    tool = None
    try:
        tool = CustomTool(**args.model_dump())
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
