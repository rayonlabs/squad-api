"""
Router for X interactions.
"""

import tweepy
from functools import lru_dict
from typing import Optional
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException, status
from squad.auth import get_current_agent
from squad.config import settings
from squad.util import encrypt, decrypt
from squad.database import get_db_session
from squad.agent.schemas import Agent

router = APIRouter()


@lru_dict(maxsize=1)
def oauth_handler():
    return tweepy.OAuthHandler(
        settings.x_api_key,
        settings.x_api_secret,
        callback=settings.x_api_callback_url,
    )


class UserActionRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)


class TweetActionRequest(BaseModel):
    tweet_id: str = Field(..., min_length=1, max_length=64)


class QuoteTweetRequest(BaseModel):
    tweet_id: str = Field(..., min_length=1, max_length=64)
    text: str = Field(..., min_length=1, max_length=400)


@router.get("/callback")
async def oauth_callback(
    oauth_token: str,
    oauth_verifier: str,
    db: AsyncSession = Depends(get_db_session),
):
    oauth = oauth_handler()
    oauth.request_token = {
        "oauth_token": oauth_token,
        "oauth_token_secret": oauth_verifier,
    }
    try:
        access_token, access_token_secret = oauth.get_access_token(oauth_verifier)
        client = tweepy.Client(
            consumer_key=settings.x_api_key,
            consumer_secret=settings.x_api_secret,
            access_token=access_token,
            access_token_secret=access_token_secret,
        )
        user = client.get_me()
        user_id = str(user.data.id)

        agent = (
            (await db.execute(select(Agent).where(Agent.x_user_id == user_id)))
            .unique()
            .scalar_one_or_none()
        )
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No agent has been created for user {user_id}, please create an agent first.",
            )
        agent.x_access_token = await encrypt(access_token)
        agent.x_secret_access_token = await encrypt(access_token_secret)
        await db.commit()
        await db.refresh()
        return {"success": True}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


def get_agent_x_client(agent: Agent):
    if not agent.x_access_token or not agent.x_access_token_secret:
        raise HTTPException(status_code=401, detail="User not authenticated")
    return tweepy.Client(
        consumer_key=settings.x_api_key,
        consumer_secret=settings.x_api_secret,
        access_token=await decrypt(agent.x_access_token),
        access_token_secret=await decrypt(agent.x_access_token_secret),
    )


@router.post("/tweet")
async def tweet(
    text: str = Form(...),
    in_reply_to: Optional[str] = Form(None),
    media: Optional[UploadFile] = File(None),
    agent: Agent = Depends(get_current_agent()),
):
    client = await get_agent_x_client(agent)
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
    agent: Agent = Depends(get_current_agent()),
):
    client = await get_agent_x_client(agent)
    try:
        response = client.follow_user(request.user_id)
        return {"success": True, "following": response.data["following"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/like")
async def like(
    request: TweetActionRequest,
    agent: Agent = Depends(get_current_agent()),
):
    client = await get_agent_x_client(agent)
    try:
        response = client.like(request.tweet_id)
        return {"success": True, "liked": response.data["liked"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/retweet")
async def retweet(
    request: TweetActionRequest,
    agent: Agent = Depends(get_current_agent()),
):
    client = await get_agent_x_client(agent)
    try:
        response = client.retweet(request.tweet_id)
        return {"success": True, "retweeted": response.data["retweeted"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/quote")
async def quote_tweet(
    request: QuoteTweetRequest,
    agent: Agent = Depends(get_current_agent()),
):
    client = await get_agent_x_client(agent)
    try:
        response = client.create_tweet(
            text=request.text,
            quote_tweet_id=request.tweet_id,
        )
        return {"tweet_id": response.data["id"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
