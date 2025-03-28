"""
Router to handle secrets.
"""

from typing import Optional, Any
from sqlalchemy import select, or_, func, exists, String
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status
from squad.auth import get_current_user
from squad.util import encrypt
from squad.database import get_db_session
from squad.pagination import PaginatedResponse
from squad.secret.schemas import BYOKSecret, BYOKSecretItem, is_valid_name
from squad.secret.requests import BYOKSecretArgs, BYOKSecretItemArgs
from squad.secret.response import BYOKSecretResponse
from squad.tool.schemas import Tool

router = APIRouter()


class PaginatedBYOKSecrets(PaginatedResponse):
    items: list[BYOKSecretResponse]


async def _load_secret(db, secret_id_or_name, user_id):
    query = (
        select(BYOKSecret)
        .where(
            or_(BYOKSecret.secret_id == secret_id_or_name, BYOKSecret.name.ilike(secret_id_or_name))
        )
        .where(or_(BYOKSecret.user_id == user_id, BYOKSecret.public.is_(True)))
    )
    return (await db.execute(query)).unique().scalar_one_or_none()


@router.get("", response_model=PaginatedBYOKSecrets)
async def list_secrets(
    db: AsyncSession = Depends(get_db_session),
    include_public: Optional[bool] = False,
    search: Optional[str] = None,
    limit: Optional[int] = 10,
    page: Optional[int] = 0,
    user: Any = Depends(get_current_user()),
):
    user_id = user.user_id if user else None
    query = select(BYOKSecret)
    if search:
        query = query.where(BYOKSecret.name.ilike(f"%{search}%"))
    if include_public:
        if user:
            query = query.where(or_(BYOKSecret.user_id == user_id, BYOKSecret.public.is_(True)))
        else:
            query = query.where(BYOKSecret.public.is_(True))
    elif not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You must authenticate to see your own private secrets.",
        )
    else:
        query = query.where(BYOKSecret.user_id == user_id)

    # Perform a count.
    total_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(total_query)
    total = total_result.scalar() or 0

    # Pagination.
    query = (
        query.order_by(BYOKSecret.created_at.desc())
        .offset((page or 0) * (limit or 10))
        .limit((limit or 10))
    )
    secrets = (await db.execute(query)).unique().scalars().all()
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "items": [BYOKSecretResponse.from_orm(item) for item in secrets],
    }


@router.get("/name_check")
async def check_secret_name(
    name: str,
    db: AsyncSession = Depends(get_db_session),
):
    if not is_valid_name(name):
        return {"valid": False, "available": False}
    query = select(exists().where(BYOKSecret.name.ilike(name)))
    secret_exists = await db.scalar(query)
    if secret_exists:
        return {"available": False, "valid": True}
    return {"available": True, "valid": True}


@router.get("/{secret_id_or_name}", response_model=BYOKSecretResponse)
async def get_secret(
    secret_id_or_name: str,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    if (secret := await _load_secret(db, secret_id_or_name, user.user_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"BYOKSecret {secret_id_or_name} not found, or is not public",
        )
    return secret


@router.post("", response_model=BYOKSecretResponse)
async def create_secret(
    args: BYOKSecretArgs,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    existing_secret = (
        (await db.execute(select(BYOKSecret).where(BYOKSecret.name.ilike(args.name))))
        .unique()
        .scalar_one_or_none()
    )
    if existing_secret:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A secret with that name already exists, pick a new name.",
        )
    try:
        secret = BYOKSecret(**args.model_dump())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request: {exc}",
        )
    db.add(secret)
    await db.commit()
    await db.refresh(secret)
    return secret


@router.post("/{secret_id_or_name}")
async def create_secret_item(
    secret_id_or_name: str,
    secret_args: BYOKSecretItemArgs,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    if (secret := await _load_secret(db, secret_id_or_name, user.user_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"BYOKSecret {secret_id_or_name} not found, or is not public",
        )
    item = BYOKSecretItem(
        secret_id=secret.secret_id,
        user_id=user.user_id,
        encrypted_value=await encrypt(secret_args.value, secret_type="byok"),
    )
    await db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/{secret_id_or_name}/{item_id}")
async def delete_secret_item(
    secret_id: str,
    item_id: str,
    secret_args: BYOKSecretItemArgs,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    item = (
        await db.execute(
            select(
                BYOKSecretItem.where(
                    BYOKSecretItem.item_id == item_id, BYOKSecretItem.user_id == user.user_id
                )
            )
        )
        .unique()
        .scalar_one_or_none()
    )
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"BYOK secret {item_id=} not found",
        )
    await db.delete(item)
    await db.commit()
    return {"item_id": item_id, "deleted": True}


@router.delete("/{secret_id_or_name}")
async def delete_secret(
    secret_id_or_name: str,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    secret = await _load_secret(db, secret_id_or_name, user.user_id)
    if not secret or secret.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"BYOKSecret {secret_id_or_name} not found, or does not belong to you.",
        )

    # Make sure no tools are using it.
    tool_exists_query = select(
        exists().where(
            func.jsonb_extract_path_text(Tool.tool_args, "secret_name").cast(String) == secret.name
        )
    )
    tool_exists = await db.execute(tool_exists_query).scalar_one()
    if tool_exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="One or more tools are referencing this secret, cannot delete.",
        )
    secret_id = secret.secret_id
    await db.delete(secret)
    await db.commit()
    return {"deleted": True, "secret_id": secret_id}
