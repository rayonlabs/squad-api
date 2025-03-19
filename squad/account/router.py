"""
Router to handle accounts.
"""

from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status
from squad.auth import get_current_user
from squad.database import get_db_session
from squad.account.schemas import AccountLimit
from squad.account.requests import AccountLimitRequest

router = APIRouter()


def limit_access(user):
    if user.user_id not in (
        "dff3e6bb-3a6b-5a2b-9c48-da3abcd5ca5f",
        "b6bb1347-6ea5-556f-8b23-50b124f3ffc8",
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have access to view/modify account limits.",
        )


@router.get("/limit/{user_id}")
async def get_account_limits(
    user_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    limit_access(user)
    query = select(AccountLimit).where(AccountLimit.user_id == user_id)
    return (await db.execute(query)).unique().scalar_one_or_none()


@router.post("/limit/{user_id}")
async def set_account_limits(
    user_id: str,
    new_limits: AccountLimitRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    limit_access(user)
    query = select(AccountLimit).where(AccountLimit.user_id == user_id)
    limit = (await db.execute(query)).unique().scalar_one_or_none()
    if limit:
        await db.delete(limit)
    args = new_limits.model_dump()
    args["user_id"] = user_id
    limit = AccountLimit(**args)
    db.add(limit)
    await db.commit()
    await db.refresh(limit)
    return limit


@router.delete("/limit/{user_id}")
async def delete_account_limit(
    user_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user()),
):
    limit_access(user)
    query = select(AccountLimit).where(AccountLimit.user_id == user_id)
    limit = (await db.execute(query)).unique().scalar_one_or_none()
    await db.delete(limit)
    await db.commit()
    return {"deleted": True, "user_id": user_id}
