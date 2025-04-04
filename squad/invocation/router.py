"""
Router to handle invocations (except the actual creation/POST call).
"""

import io
import os
import orjson as json
import asyncio
import traceback
from datetime import datetime, timedelta
from loguru import logger
from pathlib import Path
from typing import Optional, Any, Annotated
from sqlalchemy import select, or_, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
    Response,
    Header,
    Request,
    UploadFile,
    File,
)
from fastapi.responses import StreamingResponse
from squad.auth import get_current_user, get_current_agent
from squad.util import now_str
from squad.agent.schemas import Agent
from squad.config import settings
from squad.database import get_db_session
from squad.pagination import PaginatedResponse
from squad.invocation.schemas import Invocation
from squad.invocation.response import InvocationResponse

router = APIRouter()


class PaginatedInvocations(PaginatedResponse):
    items: list[InvocationResponse]


async def _load_invocation(db, invocation_id, user_id):
    query = select(Invocation).where(Invocation.invocation_id == invocation_id)
    if user_id:
        query = query.where(or_(Invocation.user_id == user_id, Invocation.public.is_(True)))
    elif user_id != "__agent__":
        query = query.where(Invocation.public.is_(True))
    return (await db.execute(query)).unique().scalar_one_or_none()


@router.get("", response_model=PaginatedInvocations)
async def list_invocations(
    db: AsyncSession = Depends(get_db_session),
    agent_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    search: Optional[str] = None,
    limit: Optional[int] = 10,
    page: Optional[int] = 0,
    user_id: Optional[str] = None,
    user: Any = Depends(get_current_user(raise_not_found=False)),
    mine: Optional[bool] = False,
):
    current_user_id = user.user_id if user else None
    query = select(Invocation)
    if mine:
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="You must authenticate to see your own private invocations.",
            )
        else:
            query = query.where(Invocation.user_id == current_user_id)
    if agent_id:
        query = query.where(Invocation.agent_id == agent_id)
    if agent_name:
        query = query.join(Agent, Invocation.agent_id == Agent.agent_id).where(
            Agent.name.ilike(agent_name)
        )
    if search:
        query = query.where(Invocation.task.ilike(f"%{search}%"))
    if user_id:
        query = query.where(Invocation.user_id == user_id)

    # Perform a count.
    total_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(total_query)
    total = total_result.scalar() or 0

    # Pagination.
    query = (
        query.order_by(Invocation.created_at.desc())
        .offset((page or 0) * (limit or 10))
        .limit((limit or 10))
    )
    result = await db.execute(query)
    items = [InvocationResponse.from_orm(item) for item in result.unique().scalars().all()]
    for item in items:
        if not item.public and item.user_id != current_user_id:
            item.invocation_id = "(private)"
            item.task = "(private)"
            item.source = "(private)"
            item.inputs = []
            item.outputs = []
            item.answer = "(private)"
            item.user_id = "(private)"
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "items": items,
    }


@router.get("/quota")
async def check_quota(
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    count = (
        await db.execute(
            select(func.count())
            .select_from(Invocation)
            .where(
                Invocation.user_id == user.user_id,
                Invocation.created_at
                >= func.now() - timedelta(seconds=user.limits.max_invocations_window),
            )
        )
    ).scalar_one()
    return {
        "count": count,
        "window": user.limits.max_invocations_window,
        "limit": user.limits.max_invocations,
    }


@router.get("/{invocation_id}", response_model=InvocationResponse)
async def get_invocation(
    invocation_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user(raise_not_found=False)),
):
    user_id = user.user_id if user else None
    if (invocation := await _load_invocation(db, invocation_id, user_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invocation {invocation_id} not found, or is not public",
        )
    return invocation


async def _download_or_render_file(
    invocation: Invocation,
    filename: str,
    file_list: list[str],
):
    target_path = None
    basename_path = None
    for f in file_list:
        if f == filename:
            target_path = f
            break
        if os.path.basename(filename) == os.path.basename(f):
            basename_path = f

    # Fallback to basename matching.
    if not target_path:
        target_path = basename_path
    if not target_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invocation {invocation.invocation_id} file {filename} not found, or is not public",
        )

    # Fetch the file.
    data = io.BytesIO()
    async with settings.s3_client() as s3:
        await s3.download_fileobj(settings.storage_bucket, target_path, data)

    # Render some types inline, others as attachment/download.
    disposition = "attachment"
    file_ext = Path(filename).suffix.lower()
    content_type_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".mp4": "video/mp4",
        ".mp3": "audio/mp3",
        ".wav": "audio/wav",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".json": "application/json",
        ".xml": "text/xml",
        ".log": "text/plain",
    }
    content_type = content_type_map.get(file_ext)
    if content_type:
        disposition = "inline"
        if content_type.startswith(("text/", "application/json")):
            content_type += "; charset=utf-8"

    headers = {
        "Content-Disposition": f'{disposition}; filename="{Path(filename).name}"',
    }
    if content_type:
        headers["Content-Type"] = content_type
    return Response(
        content=data.getvalue(),
        headers=headers,
    )


@router.get("/{invocation_id}/download/{filename:path}")
async def get_invocation_output_file(
    invocation_id: str,
    filename: str,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user(raise_not_found=False)),
):
    user_id = user.user_id if user else None
    invocation = await _load_invocation(db, invocation_id, user_id)
    if not invocation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invocation {invocation_id} output file {filename} not found, or is not public",
        )
    return await _download_or_render_file(invocation, filename, invocation.outputs)


@router.get("/{invocation_id}/inputs/{filename:path}")
async def get_input_file(
    invocation_id: str,
    filename: str,
    request: Request,
    user: Any = Depends(get_current_user(raise_not_found=False)),
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = user.user_id if user else None
    user_scope = user_id
    if not user:
        if authorization:
            await get_current_agent(issuer="squad", scopes=[invocation_id])(request, authorization)
            user_scope = "__agent__"
    invocation = await _load_invocation(db, invocation_id, user_scope)
    if not invocation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invocation {invocation_id} input file {filename} not found",
        )
    return await _download_or_render_file(invocation, filename, invocation.inputs)


@router.get("/{invocation_id}/stream")
async def stream_invocation(
    invocation_id: str,
    offset: str = None,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user(raise_not_found=False)),
):
    user_id = user.user_id if user else None
    if (invocation := await _load_invocation(db, invocation_id, user_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invocation {invocation_id} not found, or is not public",
        )
    if invocation.completed_at:
        delta = datetime.now().replace(tzinfo=None) - invocation.completed_at.replace(tzinfo=None)
        if delta >= timedelta(minutes=30):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invocation {invocation_id} has already completed, unable to stream",
            )

    # Stream logs for clients who set the "wait" flag.
    async def _stream():
        nonlocal offset, invocation
        last_offset = offset
        finished = False
        while not finished:
            stream_result = None
            try:
                stream_result = await settings.redis_client.xrange(
                    invocation.stream_key, last_offset or "-", "+"
                )
            except Exception as exc:
                print(f"Error fetching stream result: {exc}\n{traceback.format_exc()}")
                yield f"data: ERROR: {exc}"
                return
            if not stream_result:
                yield ".\n\n"
                await asyncio.sleep(1.0)
                continue
            for offset, data in stream_result:
                if finished:
                    break
                last_offset = offset.decode()
                parts = last_offset.split("-")
                last_offset = parts[0] + "-" + str(int(parts[1]) + 1)
                log_data = None
                try:
                    log_data = json.loads(data[b"data"])
                    if log_data["log"] == "__INVOCATION_FINISHED__":
                        finished = True
                except Exception:
                    ...
                if not log_data:
                    log_data = {"log": str(data[b"data"])}
                log_data["offset"] = last_offset
                if b'"log":"__INVOCATION_FINISHED__"' in data[b"data"]:
                    finished = True
                yield f"data: {json.dumps(log_data).decode()}\n\n"

    return StreamingResponse(_stream())


@router.delete("/{invocation_id}")
async def delete_invocation(
    invocation_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    invocation = await _load_invocation(db, invocation_id, user.user_id)
    if not invocation or invocation.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invocation {invocation_id} not found, or does not belong to you.",
        )
    await db.delete(invocation)
    await db.commit()
    return {"deleted": True, "invocation_id": invocation_id}


@router.post("/{invocation_id}/log")
async def append_log(
    invocation_id: str,
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    await get_current_agent(issuer="squad", scopes=[invocation_id])(request, authorization)
    invocation = await _load_invocation(db, invocation_id, "__agent__")
    if invocation.completed_at:
        return "ack"
    log = (await request.json()).get("log")
    if log and isinstance(log, str):
        await settings.redis_client.xadd(
            invocation.stream_key,
            {"data": json.dumps({"log": log, "timestamp": now_str()}).decode()},
        )

    return "ack"


@router.post("/{invocation_id}/upload")
async def upload_file(
    invocation_id: str,
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    files: Annotated[list[UploadFile], File(max_length=100)] = None,
    db: AsyncSession = Depends(get_db_session),
):
    await get_current_agent(issuer="squad", scopes=[invocation_id])(request, authorization)
    invocation = await _load_invocation(db, invocation_id, "__agent__")
    if invocation.completed_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invocation {invocation_id} has already been marked as completed.",
        )

    output_paths = []
    dt = invocation.created_at
    base_path = f"invocations/{dt.year}/{dt.month}/{dt.day}/{invocation_id}/outputs/"
    for file in files:
        logger.info(f"Attempting to upload output file to blob store: {file.filename}")
        content = await file.read()
        destination = f"{base_path}{file.filename}"
        output_paths.append(destination)
        async with settings.s3_client() as s3:
            await s3.upload_fileobj(
                io.BytesIO(content),
                settings.storage_bucket,
                destination,
            )
    update_stmt = text(
        "UPDATE invocations SET outputs = array_cat(outputs, :new_outputs) WHERE invocation_id = :invocation_id"
    )
    await db.execute(update_stmt, {"invocation_id": invocation_id, "new_outputs": output_paths})
    await db.commit()
    await db.refresh(invocation)
    return output_paths


@router.post("/{invocation_id}/complete", response_model=InvocationResponse)
async def mark_complete(
    invocation_id: str,
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    await get_current_agent(issuer="squad", scopes=[invocation_id])(request, authorization)
    invocation = await _load_invocation(db, invocation_id, "__agent__")
    if invocation.completed_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invocation {invocation_id} has already been marked as completed.",
        )
    invocation.completed_at = func.now()
    raw_json = await request.json()
    invocation.answer = raw_json.get("answer") or raw_json
    invocation.status = "success"
    await db.commit()
    await db.refresh(invocation)
    await settings.redis_client.xadd(
        invocation.stream_key,
        {"data": json.dumps({"log": "__INVOCATION_FINISHED__", "timestamp": now_str()}).decode()},
    )
    return invocation


@router.post("/{invocation_id}/fail", response_model=InvocationResponse)
async def mark_failed(
    invocation_id: str,
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    await get_current_agent(issuer="squad", scopes=[invocation_id])(request, authorization)
    invocation = await _load_invocation(db, invocation_id, "__agent__")
    if invocation.completed_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invocation {invocation_id} has already been marked as completed.",
        )
    invocation.completed_at = func.now()
    invocation.answer = await request.json()
    invocation.status = "error"
    await db.commit()
    await db.refresh(invocation)
    await settings.redis_client.xadd(
        invocation.stream_key,
        {
            "data": json.dumps(
                {
                    "log": f"Invocation encountered an error: {invocation.answer}",
                    "timestamp": now_str(),
                }
            ).decode()
        },
    )
    await settings.redis_client.xadd(
        invocation.stream_key,
        {"data": json.dumps({"log": "__INVOCATION_FINISHED__", "timestamp": now_str()}).decode()},
    )
    return invocation
