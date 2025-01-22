"""
Router for X interactions.
"""

import tweepy
from typing import Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException, status
from squad.auth import User, get_current_user
from squad.config import settings

router = APIRouter()


class UserActionRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)


class TweetActionRequest(BaseModel):
    tweet_id: str = Field(..., min_length=1, max_length=64)


class QuoteTweetRequest(BaseModel):
    tweet_id: str = Field(..., min_length=1, max_length=64)
    text: str = Field(..., min_length=1, max_length=400)


@router.post("/tweet")
async def tweet(
    text: str = Form(...),
    in_reply_to: Optional[str] = Form(None),
    media: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user()),
):
    try:
        media_ids = []
        if media:
            file_bytes = await media.read()
            media_obj = settings.tweepy_client.media_upload(
                filename=media.filename, file=file_bytes
            )
            media_ids.append(media_obj.media_id)
        response = settings.tweepy_client.create_tweet(
            text=text, in_reply_to_tweet_id=in_reply_to, media_ids=media_ids if media_ids else None
        )
        return {"tweet_id": response.data["id"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/follow")
async def follow(request: UserActionRequest):
    try:
        response = settings.tweepy_client.follow_user(request.user_id)
        return {"success": True, "following": response.data["following"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/like")
async def like(request: TweetActionRequest):
    try:
        response = settings.tweepy_client.like(request.tweet_id)
        return {"success": True, "liked": response.data["liked"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/retweet")
async def retweet(request: TweetActionRequest):
    try:
        response = settings.tweepy_client.retweet(request.tweet_id)
        return {"success": True, "retweeted": response.data["retweeted"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/quote")
async def quote_tweet(request: QuoteTweetRequest):
    try:
        response = settings.tweepy_client.create_tweet(
            text=request.text,
            quote_tweet_id=request.tweet_id,
        )
        return {"tweet_id": response.data["id"]}
    except tweepy.TweepyException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
