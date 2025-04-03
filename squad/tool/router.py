"""
Router to handle tools.
"""

import re
from pydantic import BaseModel, Field
from loguru import logger
import squad.tool.builtin as builtin_tools
from typing import Optional, Any
from sqlalchemy import select, or_, func, exists
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status
from squad.auth import get_current_user
from squad.util import validate_logo
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


class ToolUpdateArgs(BaseModel):
    description: Optional[str] = Field(
        None,
        description="Human readable description of the function",
    )
    logo_id: Optional[str] = Field(None, description="Logo ID")
    tool_args: Optional[dict] = Field(
        None,
        description="Arguments for the tool",
    )
    code: Optional[str] = Field(None, description="Source code for custom tools")
    public: Optional[bool] = Field(None, description="Public tool")


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


@router.get("/name_check")
async def check_tool_name(
    name: str,
    db: AsyncSession = Depends(get_db_session),
):
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
        return {"valid": False, "available": False}
    query = select(exists().where(Tool.name.ilike(name)))
    tool_exists = await db.scalar(query)
    if tool_exists:
        return {"available": False, "valid": True}
    return {"available": True, "valid": True}


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
    count = (
        await db.execute(select(func.count()).select_from(Tool).where(Tool.user_id == user.user_id))
    ).scalar_one()
    if count >= user.limits.max_tools:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"You have reached or exceeded the maximum number of tools for your account tier: {count}",
        )

    args.tool_args["tool_name"] = args.name
    if not args.tool_args.get("tool_description"):
        args.tool_args["tool_description"] = args.description
    if args.template is None and not user.limits.allow_custom_tools:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="You need a higher service tier to use custom code",
        )

    # Check the logo.
    await validate_logo(args.logo_id)

    validator = ToolValidator(db, args, user)
    await validator.validate()
    tool = None
    try:
        tool = Tool(**args.model_dump())
        tool.user_id = user.user_id
    except ValueError as exc:
        import traceback

        logger.error(f"Validation error: {exc}\n{traceback.format_exc()}")
        logger.error(args.model_dump())
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return tool


@router.put("/{tool_id}", response_model=ToolResponse)
async def update_tool(
    tool_id: str,
    args: ToolUpdateArgs,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    tool = await _load_tool(db, tool_id, user.user_id)
    if not tool or tool.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool {tool_id} not found, or does not belong to you.",
        )
    update_data = args.model_dump(exclude_unset=True, exclude_none=True)
    if "tool_args" in update_data:
        update_data["tool_args"]["tool_name"] = tool.name
        if "description" in update_data and "tool_description" not in update_data["tool_args"]:
            update_data["tool_args"]["tool_description"] = update_data["description"]

    # Check the logo.
    await validate_logo(args.logo_id)

    validator_args = ToolArgs(
        name=tool.name,
        description=update_data.get("description", tool.description),
        template=tool.template,
        code=args.code,
        public=args.public,
        logo_id=args.logo_id,
        tool_args=update_data["tool_args"],
    )
    validator = ToolValidator(db, validator_args, user)
    await validator.validate()
    for key, value in update_data.items():
        setattr(tool, key, value)
    if args.code:
        tool.code = args.code
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
