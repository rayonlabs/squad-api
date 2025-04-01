"""
X/twitter tools.
"""

import os
import requests
import asyncio
from smolagents import Tool
from squad.util import rerank
from squad.data.schemas import XSearchParams
from squad.storage.x import Tweet
from squad.agent_config import settings


class XSearcher(Tool):
    name = "x_search"
    description = "Tool for performing searches on X (formerly twitter) to find information and media that might be related to a topic."
    inputs = {
        "query": {
            "type": "string",
            "description": "search query string to use when performing the search",
        },
        "top_n": {
            "type": "integer",
            "nullable": True,
            "description": "perform reranking to return only the top top_n related tweets",
        },
        "kwargs": {
            "type": "object",
            "description": (
                "Optional search flags/settings to augment, limit, or filter results. "
                "Treat this as normal python kwargs, not a dict. "
                "Supported kwargs are the following (but do not include 'query'): "
                f"{XSearchParams.model_json_schema()}\n"
                "Be sure to pass `has=['photo']` when searching for images."
            ),
        },
    }
    output_type = "string"

    def forward(self, query: str, top_n: int = 5, **kwargs):
        params = {"text": query}
        params.update(kwargs)
        raw_response = requests.post(
            f"{settings.squad_api_base_url}/data/x/search",
            json=params,
            headers={
                "Authorization": settings.authorization,
            },
        )
        tweets = raw_response.json()
        if tweets:
            tweets = [Tweet(**item) for item in raw_response.json()]
            singular_items = []
            for tweet in tweets:
                singular_items.append(
                    "\n".join(
                        [f"{key}: {value}" for key, value in tweet.model_dump().items()]
                        + [f"URL: https://x.com/i/status/{tweet.id}"]
                    )
                )
            return_docs = singular_items
            if top_n:
                loop = asyncio.get_event_loop()
                return_docs = loop.run_until_complete(
                    rerank(query, singular_items, top_n=top_n, auth=settings.authorization)
                )
            return "\n---\n".join(return_docs)


class XTweeter(Tool):
    name = "x_tweet"
    description = "Tool for creating an X post (aka tweet)."
    inputs = {
        "text": {
            "type": "string",
            "description": "search query string to use when performing the search",
        },
        "in_reply_to": {
            "type": "string",
            "description": "ID of the tweet/X post this is a reply to, if it is a reply, which is the 'id_num' field of the original input tweet",
            "nullable": True,
        },
        "media": {
            "type": "string",
            "nullable": True,
            "description": "Full path to an image or video file to include as attachment in the post.",
        },
    }
    output_type = "string"

    def forward(self, text: str, in_reply_to: str = None, media: str = None):
        payload = {
            "text": text,
        }
        if in_reply_to:
            payload["in_reply_to"] = in_reply_to
        request_args = {
            "url": f"{settings.squad_api_base_url}/x/tweet",
            "headers": {"Authorization": settings.authorization},
        }
        media_ids = []
        if media:
            if (
                isinstance(media, str)
                and os.path.exists(media)
                and media.endswith(("png", "jpg", "jpeg", "gif", "mp4", "webp"))
            ):
                media_upload_url = f"{settings.squad_api_base_url}/x/media"
                media_headers = {"Authorization": settings.authorization}
                try:
                    with open(media, "rb") as f:
                        files = {"file": (os.path.basename(media), f)}
                        media_response = requests.post(
                            media_upload_url, headers=media_headers, files=files
                        )
                    media_response.raise_for_status()
                    response_data = media_response.json()
                    if "media_id" in response_data:
                        media_id = response_data["media_id"]
                        media_ids.append(media_id)
                        print(f"Media uploaded successfully. Media ID: {media_id}")
                    else:
                        print(
                            f"Warning: Media upload request succeeded, but 'media_id' not found in response: {media_response.text}"
                        )
                except requests.exceptions.HTTPError as err:
                    print(f"HTTP Error occurred during media upload: {err}")
                    print(f"Status Code: {err.response.status_code}")
                    print(f"Response Body: {err.response.text}")
                    raise Exception(f"Failed to upload media file {media}") from err
                except requests.exceptions.RequestException as e:
                    print(f"Other request error during media upload: {e}")
                    raise Exception(f"Failed to upload media file {media}") from e
                except (IOError, OSError) as e:
                    print(f"File error reading media {media}: {e}")
                    raise Exception(f"Failed to read media file {media}") from e
                except KeyError as e:
                    print(
                        f"Error parsing media upload response: Missing key {e}. Response: {media_response.text}"
                    )
                    raise Exception(f"Failed to parse media upload response for {media}") from e
                except Exception as e:
                    print(f"An unexpected error occurred during media processing: {e}")
                    raise Exception(f"Failed to process media file {media}") from e

                # Add the successfully obtained media IDs to the main tweet payload
                if media_ids:
                    payload["media_ids"] = media_ids
            else:
                if not isinstance(media, str):
                    reason = "it is not a string path"
                elif not os.path.exists(media):
                    reason = f"file does not exist at path: {media}"
                else:
                    reason = "it is not a supported file type (png, jpg, jpeg, gif, mp4, webp)"
                raise Exception(f"Invalid media provided, {reason}!")

        request_args["json"] = payload
        response = requests.post(**request_args)
        try:
            response.raise_for_status()
            return f"Successfully tweeted: {response.text}"
        except requests.exceptions.HTTPError as err:
            print(f"HTTP Error occurred posting tweet: {err}")
            print(f"Status Code: {err.response.status_code}")
            print(f"Response Body: {err.response.text}")
            raise err  # Re-raise the original error
        except requests.exceptions.RequestException as e:
            print(f"Other request error posting tweet: {e}")
            raise e  # Re-raise the original error
        except Exception as e:  # Catch potential JSON parsing errors etc.
            print(f"An unexpected error occurred after posting tweet: {e}")
            print(f"Response status: {response.status_code}")
            print(f"Response text: {response.text}")
            raise e


class XFollower(Tool):
    name = "x_follow"
    description = "Tool for following another user on X/twitter."
    inputs = {
        "user_id": {
            "type": "string",
            "description": "ID of the user to follow",
        },
    }
    output_type = "string"

    def forward(self, user_id: str):
        response = requests.post(
            f"{settings.squad_api_base_url}/x/follow",
            json={"user_id": user_id},
            headers={
                "Authorization": settings.authorization,
            },
        )
        response.raise_for_status()
        return f"Successfully followed {user_id=}: {response.text}"


class XLiker(Tool):
    name = "x_like"
    description = "Tool to 'like' a post on X/twitter."
    inputs = {
        "tweet_id": {
            "type": "string",
            "description": "ID of the tweet to like, which is the 'id_num' value of the original input tweet",
        },
    }
    output_type = "string"

    def forward(self, tweet_id: str):
        response = requests.post(
            f"{settings.squad_api_base_url}/x/like",
            json={"tweet_id": tweet_id},
            headers={
                "Authorization": settings.authorization,
            },
        )
        response.raise_for_status()
        return f"Successfully liked {tweet_id=}: {response.text}"


class XRetweeter(Tool):
    name = "x_retweet"
    description = "Tool for re-tweeting a tweet, with no additional text/comment just a re-tweet."
    inputs = {
        "tweet_id": {
            "type": "string",
            "description": "ID of the tweet to re-tweet, which is the 'id_num' value of the original input tweet",
        },
    }
    output_type = "string"

    def forward(self, tweet_id: str):
        response = requests.post(
            f"{settings.squad_api_base_url}/x/retweet",
            json={"tweet_id": tweet_id},
            headers={
                "Authorization": settings.authorization,
            },
        )
        response.raise_for_status()
        return f"Successfully retweeted {tweet_id=}: {response.text}"


class XQuoteTweeter(Tool):
    name = "x_quote_tweet"
    description = "Tool for re-tweeting a tweet with additional comments/text."
    inputs = {
        "tweet_id": {
            "type": "string",
            "description": "ID of the tweet to re-tweet, which is the 'id_num' value of the original input tweet",
        },
        "text": {
            "type": "string",
            "description": "The text you want to add with the retweet, i.e. your comments on the original post",
        },
    }
    output_type = "string"

    def forward(self, tweet_id: str, text: str):
        response = requests.post(
            f"{settings.squad_api_base_url}/x/like",
            json={"tweet_id": tweet_id, "text": text},
            headers={
                "Authorization": settings.authorization,
            },
        )
        response.raise_for_status()
        return f"Successfully quote tweeted {tweet_id=}: {response.text}"
