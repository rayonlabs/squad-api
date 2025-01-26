"""
Router for X interactions.
"""

import time
import tweepy
from functools import lru_cache
from typing import Optional
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException, status, Request
from fastapi.responses import RedirectResponse
from squad.auth import get_current_agent
from squad.config import settings
from squad.util import encrypt, decrypt
from squad.database import get_db_session
from squad.agent.schemas import Agent

router = APIRouter()


@lru_cache(maxsize=1)
def oauth_handler():
    return tweepy.OAuth2UserHandler(
        client_id=settings.x_client_id,
        redirect_uri=settings.x_api_callback_url,
        scope=[
            "tweet.read",
            "tweet.write",
            "users.read",
            "follows.read",
            "follows.write",
            "like.read",
            "like.write",
            "mute.read",
            "mute.write",
            "block.read",
            "block.write",
            "offline.access",
            "media.write",
            "bookmark.read",
            "bookmark.write",
        ],
        client_secret=settings.x_client_secret,
    )


class UserActionRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)


class TweetActionRequest(BaseModel):
    tweet_id: str = Field(..., min_length=1, max_length=64)


class QuoteTweetRequest(BaseModel):
    tweet_id: str = Field(..., min_length=1, max_length=64)
    text: str = Field(..., min_length=1, max_length=400)


@router.get("/auth")
async def get_oauth_url():
    oauth = oauth_handler()
    auth_url = oauth.get_authorization_url()
    return RedirectResponse(url=auth_url)


@router.get("/callback")
async def oauth_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    oauth = oauth_handler()
    try:
        access_token = oauth.fetch_token(request.url._url)
        client = tweepy.Client(access_token["access_token"])
        user = client.get_me(user_auth=False)
        user_id = str(user.data.id)
        agent = (
            (await db.execute(select(Agent).where(Agent.x_user_id == user_id)))
            .unique()
            .scalar_one_or_none()
        )
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You must create an agent first with an x_user_id or x_username!",
            )
        agent.x_access_token = await encrypt(access_token["access_token"])
        agent.x_refresh_token = await encrypt(access_token["refresh_token"])
        agent.x_token_expires_at = access_token["expires_at"]
        await db.commit()
        await db.refresh(agent)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return RedirectResponse(url=f"{settings.squad_base_url}")


async def get_agent_x_client(db: AsyncSession, agent: Agent):
    if not agent.x_access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not authenticated"
        )

    if time.time() > agent.x_token_expires_at:
        oauth = oauth_handler()
        new_token = oauth.refresh_token(
            client_id=settings.x_client_id,
            refresh_token=await decrypt(agent.x_refresh_token),
        )
        agent.x_access_token = await encrypt(new_token["access_token"])
        agent.x_refresh_token = await encrypt(new_token["refresh_token"])
        agent.x_token_expires_at = new_token["expires_at"]
        await db.commit()
        await db.refresh(agent)

    return tweepy.Client(await decrypt(agent.x_access_token))


@router.post("/tweet")
async def tweet(
    text: str = Form(...),
    in_reply_to: Optional[str] = Form(None),
    media: Optional[UploadFile] = File(None),
    agent: Agent = Depends(get_current_agent(scopes=["x"])),
    db: AsyncSession = Depends(get_db_session),
):
    client = await get_agent_x_client(db, agent)
    try:
        media_ids = []
        if media:
            file_bytes = await media.read()
            media_obj = client.media_upload(filename=media.filename, file=file_bytes)
            media_ids.append(media_obj.media_id)
        response = client.create_tweet(
            text=text, in_reply_to_tweet_id=in_reply_to, media_ids=media_ids if media_ids else None
        )
        return {"tweet_id": response.data["id"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/follow")
async def follow(
    request: UserActionRequest,
    agent: Agent = Depends(get_current_agent(scopes=["x"])),
    db: AsyncSession = Depends(get_db_session),
):
    client = await get_agent_x_client(db, agent)
    try:
        response = client.follow_user(request.user_id)
        return {"success": True, "following": response.data["following"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/like")
async def like(
    request: TweetActionRequest,
    agent: Agent = Depends(get_current_agent(scopes=["x"])),
    db: AsyncSession = Depends(get_db_session),
):
    client = await get_agent_x_client(db, agent)
    try:
        response = client.like(request.tweet_id)
        return {"success": True, "liked": response.data["liked"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/retweet")
async def retweet(
    request: TweetActionRequest,
    agent: Agent = Depends(get_current_agent(scopes=["x"])),
    db: AsyncSession = Depends(get_db_session),
):
    client = await get_agent_x_client(db, agent)
    try:
        response = client.retweet(request.tweet_id)
        return {"success": True, "retweeted": response.data["retweeted"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/quote")
async def quote_tweet(
    request: QuoteTweetRequest,
    agent: Agent = Depends(get_current_agent(scopes=["x"])),
    db: AsyncSession = Depends(get_db_session),
):
    client = await get_agent_x_client(db, agent)
    try:
        response = client.create_tweet(
            text=request.text,
            quote_tweet_id=request.tweet_id,
        )
        return {"tweet_id": response.data["id"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
