"""
Router for X interactions.
"""

import time
import hashlib
import tweepy
import secrets
import magic
import mimetypes
import aiohttp
import pybase64 as base64
from loguru import logger
from typing import Optional
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status, Request, File, UploadFile
from fastapi.responses import RedirectResponse
from squad.auth import get_current_agent
from squad.config import settings
from squad.util import encrypt, decrypt, contains_hate_speech, contains_nsfw
from squad.database import get_db_session
from squad.agent.schemas import Agent

router = APIRouter()

MEDIA_CATEGORIES = {"image": "tweet_image", "gif": "tweet_gif", "video": "tweet_video"}
SUPPORTED_IMAGE_TYPES = [
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/tiff",
    "image/bmp",
    "image/svg+xml",
]
SUPPORTED_GIF_TYPES = ["image/gif"]
SUPPORTED_VIDEO_TYPES = ["video/mp4"]
SCOPE = [
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
]


class TweetPayload(BaseModel):
    text: str
    in_reply_to: int
    media_ids: Optional[list[int]] = []


async def get_content_type_from_filename(filename: str) -> str:
    """
    Get content type from filename if the provided content_type is missing or generic.
    """
    content_type, _ = mimetypes.guess_type(filename)
    return content_type


async def determine_media_category(content_type: str, filename: str = None) -> str:
    """
    Determine the appropriate media category based on content type with filename fallback.
    """
    if not content_type or content_type == "application/octet-stream":
        if not filename:
            raise HTTPException(
                status_code=400,
                detail="Cannot determine media type: both content_type and filename are invalid",
            )
        content_type = await get_content_type_from_filename(filename)
        if not content_type:
            raise HTTPException(
                status_code=400,
                detail=f"Could not determine content type from filename: {filename}",
            )
    content_type = content_type.lower()
    if any(content_type.startswith(t) for t in SUPPORTED_IMAGE_TYPES):
        return MEDIA_CATEGORIES["image"]
    elif any(content_type == t for t in SUPPORTED_GIF_TYPES):
        return MEDIA_CATEGORIES["gif"]
    elif any(content_type.startswith(t) for t in SUPPORTED_VIDEO_TYPES):
        return MEDIA_CATEGORIES["video"]
    raise HTTPException(status_code=400, detail=f"Unsupported media type: {content_type}")


def oauth_handler():
    return tweepy.OAuth2UserHandler(
        client_id=settings.x_client_id,
        redirect_uri=settings.x_api_callback_url,
        scope=SCOPE,
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
    code_verifier = secrets.token_urlsafe(64)
    code_challenge_bytes = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(code_challenge_bytes).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    auth_url = (
        "https://x.com/i/oauth2/authorize"
        "?response_type=code"
        f"&client_id={settings.x_client_id}"
        f"&redirect_uri={settings.x_api_callback_url}"
        f"&scope={' '.join(SCOPE)}"
        f"&state={state}"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )
    await settings.redis_client.set(f"xstate:{state}", code_verifier, ex=900)
    logger.info(f"Stored code_verifier for state {state}: {code_verifier}")
    if redirect_path:
        await settings.redis_client.set(f"xredirect:{state}", redirect_path, ex=600)
    return RedirectResponse(url=auth_url)


@router.get("/callback")
async def oauth_callback(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    state: Optional[str] = None,
    code: Optional[str] = None,
    error: Optional[str] = None,
):
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
    if isinstance(redirect_path, bytes):
        redirect_path = redirect_path.decode()
    if isinstance(code_verifier, bytes):
        code_verifier = code_verifier.decode()
    try:
        token_url = "https://api.x.com/2/oauth2/token"
        client_credentials = f"{settings.x_client_id}:{settings.x_client_secret}"
        client_credentials_b64 = base64.b64encode(client_credentials.encode()).decode()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {client_credentials_b64}",
        }
        data = {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.x_api_callback_url,
            "code_verifier": code_verifier,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, headers=headers, data=data) as response:
                if not 200 <= response.status < 300:
                    error_text = await response.text()
                    logger.warning(f"Token request failed: {response.status} - {error_text}")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Failed to fetch access token: {error_text}",
                    )
                access_token = await response.json()
        client = tweepy.Client(access_token["access_token"])
        user = client.get_me(user_auth=False)
        user_id = str(user.data.id)
        x_username = user.data.username
        agent = (
            (await db.execute(select(Agent).where(Agent.x_username.ilike(x_username))))
            .unique()
            .scalar_one_or_none()
        )
        if not agent:
            logger.error(f"No agent found for X user ID: {user_id} username={x_username}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No agent found for X user ID {user_id}. Please ensure an agent profile exists with this X user ID before authenticating.",
            )
        agent.x_user_id = user_id
        agent.x_access_token = await encrypt(access_token["access_token"])
        agent.x_refresh_token = await encrypt(access_token["refresh_token"])
        try:
            agent.x_token_expires_at = access_token["expires_at"]
        except Exception:
            agent.x_token_expires_at = time.time() + access_token["expires_in"]
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

    return RedirectResponse(url=f"{settings.squad_base_url}/{redirect_path.lstrip('/')}")


async def get_agent_x_client(db: AsyncSession, agent: Agent):
    if not agent.x_access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not authenticated"
        )

    if time.time() > agent.x_token_expires_at:
        # Reload agent.
        agent = (
            (await db.execute(select(Agent).where(Agent.agent_id == agent.agent_id)))
            .unique()
            .scalar_one_or_none()
        )
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


@router.post("/media")
async def upload_media(
    file: UploadFile = File(...),
    agent: Agent = Depends(get_current_agent(scopes=["x"])),
    db: AsyncSession = Depends(get_db_session),
):
    await get_agent_x_client(db, agent)

    # Get file content and determine size
    file_content = await file.read()
    total_bytes = len(file_content)

    # Detect content type.
    content_type = file.content_type

    sample = file_content[:2048]
    detected_mime = magic.Magic(mime=True).from_buffer(sample)
    if detected_mime and detected_mime != "application/octet-stream":
        content_type = detected_mime
    media_category = await determine_media_category(content_type, file.filename)

    if media_category == "tweet_image" and total_bytes > 5 * 1024 * 1024:
        return {"error": "Image file size exceeds the 5MB limit"}
    elif media_category == "tweet_gif" and total_bytes > 15 * 1024 * 1024:
        return {"error": "GIF file size exceeds the 15MB limit"}
    elif media_category == "tweet_video" and total_bytes > 512 * 1024 * 1024:
        return {"error": "Video file size exceeds the 512MB limit"}
    await file.seek(0)

    # NSFW?
    if await contains_nsfw(file_content, content_type):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Media appears to contain NSFW content.",
        )

    # Use the agent's X access token.
    access_token = await decrypt(agent.x_access_token)
    headers = {"Authorization": f"Bearer {access_token}"}
    async with aiohttp.ClientSession() as session:
        # INIT required to get a media ID to use.
        init_data = {
            "command": "INIT",
            "total_bytes": str(total_bytes),
            "media_type": content_type,
            "media_category": media_category,
        }
        async with session.post(
            "https://api.twitter.com/2/media/upload", data=init_data, headers=headers
        ) as init_response:
            if not 200 <= init_response.status < 300:
                error_text = await init_response.text()
                return {"error": f"Failed to initialize media upload: {error_text}"}
            init_result = await init_response.json()
            media_id = init_result.get("data", {}).get("id")
            if not media_id:
                return {"error": "Failed to get media_id from initialization"}

        # Now upload the actual media via APPEND, either single post or chunked.
        CHUNK_SIZE = 5 * 1024 * 1024
        if total_bytes <= CHUNK_SIZE:
            form_data = aiohttp.FormData()
            form_data.add_field("command", "APPEND")
            form_data.add_field("media_id", media_id)
            form_data.add_field("segment_index", "0")
            form_data.add_field(
                "media", file_content, filename=file.filename, content_type=content_type
            )
            async with session.post(
                "https://api.twitter.com/2/media/upload", data=form_data, headers=headers
            ) as append_response:
                if not 200 <= append_response.status < 300:
                    error_text = await append_response.text()
                    return {"error": f"Failed to append media: {error_text}"}
        else:
            chunks = (total_bytes + CHUNK_SIZE - 1) // CHUNK_SIZE
            for i in range(chunks):
                start_byte = i * CHUNK_SIZE
                end_byte = min((i + 1) * CHUNK_SIZE, total_bytes)
                chunk_data = file_content[start_byte:end_byte]
                form_data = aiohttp.FormData()
                form_data.add_field("command", "APPEND")
                form_data.add_field("media_id", media_id)
                form_data.add_field("segment_index", str(i))
                form_data.add_field(
                    "media", chunk_data, filename=file.filename, content_type=content_type
                )
                async with session.post(
                    "https://api.twitter.com/2/media/upload", data=form_data, headers=headers
                ) as append_response:
                    if not 200 <= append_response.status < 300:
                        error_text = await append_response.text()
                        return {
                            "error": f"Failed to append media chunk {i+1}/{chunks}: {error_text}"
                        }

        # async with session.post(
        #    "https://api.twitter.com/2/media/upload",
        #    data=form_data,
        #    headers=headers
        # ) as append_response:
        #    if append_response.status != 200 and append_response.status != 204:
        #        error_text = await append_response.text()
        #        return {"error": f"Failed to append media: {error_text}"}

        # FINALIZE the media upload.
        finalize_data = {"command": "FINALIZE", "media_id": media_id}
        async with session.post(
            "https://api.twitter.com/2/media/upload", data=finalize_data, headers=headers
        ) as finalize_response:
            if not 200 <= finalize_response.status < 3090:
                error_text = await finalize_response.text()
                return {"error": f"Failed to finalize media upload: {error_text}"}
            finalize_result = await finalize_response.json()
            processing_info = finalize_result.get("data", {}).get("processing_info")
            if processing_info:
                processing_state = processing_info.get("state")
                if processing_state == "pending" or processing_state == "in_progress":
                    return {
                        "media_id": media_id,
                        "status": "processing",
                        "check_after_secs": processing_info.get("check_after_secs", 0),
                        "progress_percent": processing_info.get("progress_percent", 0),
                    }
                elif processing_state == "failed":
                    return {
                        "error": "Media processing failed",
                        "media_id": media_id,
                        "details": processing_info.get("error", {}),
                    }
            return {
                "media_id": media_id,
                "status": "completed",
                "expires_after_secs": finalize_result.get("data", {}).get(
                    "expires_after_secs", 86400
                ),
            }


@router.post("/tweet")
async def tweet(
    tweet_payload: TweetPayload,
    agent: Agent = Depends(get_current_agent(scopes=["x"])),
    db: AsyncSession = Depends(get_db_session),
):
    # Block hate speech and NSFW media, otherwise allow.
    if await contains_hate_speech([tweet_payload.text]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Hate speech detected: {tweet_payload.text}",
        )

    client = await get_agent_x_client(db, agent)
    if not tweet_payload.media_ids:
        tweet_payload.media_ids = None
    response = client.create_tweet(
        text=tweet_payload.text,
        in_reply_to_tweet_id=tweet_payload.in_reply_to,
        media_ids=tweet_payload.media_ids,
        user_auth=False,
    )
    return {"tweet_id": response.data["id"]}


@router.post("/follow")
async def follow(
    request: UserActionRequest,
    agent: Agent = Depends(get_current_agent(scopes=["x"])),
    db: AsyncSession = Depends(get_db_session),
):
    client = await get_agent_x_client(db, agent)
    try:
        response = client.follow_user(request.user_id, user_auth=False)
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
        response = client.like(request.tweet_id, user_auth=False)
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
        response = client.retweet(request.tweet_id, user_auth=False)
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
            text=request.text, quote_tweet_id=request.tweet_id, user_auth=False
        )
        return {"tweet_id": response.data["id"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
