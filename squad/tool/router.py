"""
Router to handle tools.
"""

import squad.tool.builtin as builtin_tools
from typing import Optional, Any
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status
from squad.auth import get_current_user
from squad.database import get_db_session
from squad.pagination import PaginatedResponse
from squad.tool.schemas import Tool
from squad.tool.requests import ToolArgs
from squad.tool.response import ToolResponse
from squad.tool.validation import (
    ToolValidator,
    ImageArgs,
    LLMArgs,
    TTSArgs,
    MemoryArgs,
    VLMArgs,
)

router = APIRouter()

TOOL_MAP = {
    "vlm_tool": VLMArgs,
    "llm_tool": LLMArgs,
    "image_tool": ImageArgs,
    "tts_tool": TTSArgs,
}


class PaginatedTools(PaginatedResponse):
    items: list[ToolResponse]


async def _load_tool(db, tool_id, user_id):
    query = select(Tool).where(Tool.tool_id == tool_id)
    if user_id:
        query = query.where(or_(Tool.user_id == user_id, Tool.public.is_(True)))
    else:
        query = query.where(Tool.public.is_(True))
    return (await db.execute(query)).unique().scalar_one_or_none()


@router.get("/options")
async def list_options():
    model_dump = ToolArgs.model_json_schema()
    template_options = {}
    for key in dir(builtin_tools):
        obj = getattr(builtin_tools, key)
        if isinstance(obj, type) and obj != builtin_tools.Tool:
            template_options[key] = None
        elif obj != builtin_tools.Tool:
            if key.startswith("memory_"):
                template_options[key] = MemoryArgs.model_json_schema()
            elif (arg_class := TOOL_MAP.get(key)) is not None:
                template_options[key] = arg_class.model_json_schema()
    return {
        "request_schema": model_dump,
        "tool_args": template_options,
    }


@router.get("", response_model=PaginatedTools)
async def list_tools(
    db: AsyncSession = Depends(get_db_session),
    include_public: Optional[bool] = False,
    search: Optional[str] = None,
    limit: Optional[int] = 10,
    page: Optional[int] = 0,
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

    # Perform a count.
    total_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(total_query)
    total = total_result.scalar() or 0

    # Pagination.
    query = (
        query.order_by(Tool.created_at.desc())
        .offset((page or 0) * (limit or 10))
        .limit((limit or 10))
    )
    result = await db.execute(query)
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "items": [ToolResponse.from_orm(item) for item in result.unique().scalars().all()],
    }


@router.get("/{tool_id}", response_model=ToolResponse)
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


@router.post("", response_model=ToolResponse)
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
