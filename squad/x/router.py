"""
Router for X interactions.
"""

import time
import tweepy
import secrets
from yarl import URL
from loguru import logger
from functools import lru_cache
from typing import Optional
from pydantic import BaseModel, Field
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException, status, Request
from fastapi.responses import RedirectResponse
from squad.auth import get_current_agent
from squad.config import settings
from squad.util import encrypt, decrypt, contains_nsfw, contains_hate_speech
from squad.database import get_db_session
from squad.agent.schemas import Agent

router = APIRouter()

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
ALLOWED_VIDEO_TYPES = {"video/mp4"}


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
async def get_oauth_url(redirect_path: Optional[str] = None):
    oauth = oauth_handler()
    code_verifier = secrets.token_urlsafe(64)
    try:
        auth_url = oauth.get_authorization_url(code_verifier=code_verifier)
    except TypeError:
        try:
            auth_url = oauth.get_authorization_url()
        except Exception as e:
            logger.error(f"Failed to get authorization URL: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create authorization URL",
            )

    parsed_url = URL(auth_url)
    state = parsed_url.query.get("state")
    await settings.redis_client.set(f"xstate:{state}", code_verifier)
    if redirect_path:
        await settings.redis_client.set(f"xredirect:{state}", redirect_path)
    return RedirectResponse(url=auth_url)


@router.get("/callback")
async def oauth_callback(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    state: Optional[str] = None,
    code: Optional[str] = None,
    error: Optional[str] = None,
):
    oauth = oauth_handler()
    callback_url = request.url._url
    if error:
        error_description = request.query_params.get(
            "error_description", "No description provided."
        )
        logger.error(f"OAuth error from Twitter: {error} - {error_description}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Authentication failed: {error}. Description: {error_description}",
        )

    if not state or not code:
        logger.warning(f"Missing state or code in callback. State: {state}, Code: {code}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication callback is missing required parameters (state or code).",
        )

    code_verifier = await settings.redis_client.get(f"xstate:{state}")
    if not code_verifier:
        logger.warning(f"State parameter '{state}' not found or expired in cache.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired authentication session state. Please try authenticating again.",
        )
    await settings.redis_client.delete(f"xstate:{state}")
    redirect_path = await settings.redis_client.get(f"xredirect:{state}")
    if not redirect_path:
        redirect_path = "/?x_auth_success=true"
    if isinstance(code_verifier, bytes):
        code_verifier = code_verifier.decode()
    parsed_url = URL(callback_url)
    if "code_verifier" not in parsed_url.query:
        callback_url = str(
            parsed_url.update_query({"code_verifier": code_verifier, **dict(parsed_url.query)})
        )
        logger.info(f"Updated callback URL to include {code_verifier=}")
    try:
        access_token = oauth.fetch_token(callback_url)
        client = tweepy.Client(access_token["access_token"])
        user = client.get_me(user_auth=False)
        user_id = str(user.data.id)
        x_username = user.data.username
        agent = (
            (
                await db.execute(
                    select(Agent).where(
                        or_(Agent.x_user_id == user_id, Agent.x_username.ilike(x_username))
                    )
                )
            )
            .unique()
            .scalar_one_or_none()
        )
        if not agent:
            logger.error(f"No agent found for X user ID: {user_id} username={x_username}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No agent found for X user ID {user_id}. Please ensure an agent profile exists with this X user ID before authenticating.",
            )

        agent.x_access_token = await encrypt(access_token["access_token"])
        agent.x_refresh_token = await encrypt(access_token["refresh_token"])
        agent.x_token_expires_at = access_token["expires_at"]
        await db.commit()
        await db.refresh(agent)
        logger.info(
            f"Successfully authenticated and updated tokens for agent {agent.agent_id} (X User ID: {user_id})"
        )
    except Exception as exc:
        logger.warning(f"Error setting X auth credentials for agent: {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error setting X auth credentials for agent: {exc}",
        )
    return RedirectResponse(url=f"{settings.squad_base_url}{redirect_path}")


async def get_agent_x_client(db: AsyncSession, agent: Agent):
    if not agent.x_access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not authenticated"
        )

    if time.time() > agent.x_token_expires_at:
        # Reload agent.
        agent = (await db.execute(select(Agent).where(Agent.agent_id == agent.agent_id))).unique().scalar_one_or_none()
        oauth = oauth_handler()
        new_token = oauth.refresh_token(
            token_url="https://api.x.com/2/oauth2/token",
            client_id=settings.x_client_id,
            client_secret=settings.x_client_secret,
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
    # Block hate speech and NSFW media, otherwise allow.
    if await contains_hate_speech([text]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Hate speech detected: {text}",
        )

    client = await get_agent_x_client(db, agent)
    try:
        media_ids = []
        if media:
            if media.content_type not in ALLOWED_IMAGE_TYPES | ALLOWED_VIDEO_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported media type: {media.content_type}",
                )
            is_image = media.content_type in ALLOWED_IMAGE_TYPES
            file_bytes = await media.read()
            if is_image:
                if await contains_nsfw(file_bytes):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="NSFW content detected in media: {media.filename}",
                    )
            else:
                # XXX TODO filter videos...
                logger.warning(f"TODO add checks for NSFW on {media.filename}")
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
    if await contains_hate_speech([request.text]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Hate speech detected: {request.text}",
        )
    client = await get_agent_x_client(db, agent)
    try:
        response = client.create_tweet(
            text=request.text,
            quote_tweet_id=request.tweet_id,
        )
        return {"tweet_id": response.data["id"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
