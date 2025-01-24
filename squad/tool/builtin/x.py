"""
X/twitter tools.
"""

import os
import requests
from smolagents import Tool
from squad.data.schemas import XSearchParams
from squad.storage.x import Tweet
from squad.agent_config import settings


class XSearcher(Tool):
    name = "x_search"
    description = "Tool for performing searches on X (formerly twitter) to find information and media that might be related to a topic."
    inputs = {
        "text": {
            "type": "string",
            "description": "search query string to use when performing the search",
        },
        "extra_arguments": {
            "type": "object",
            "description": (
                "Optional search flags/settings to augment, limit, or filter results. "
                "Must be passed as a dict with key value pairs, where values are always strings. "
                "Supported extra_argument values are the following (but do not include 'text'): "
                f"{XSearchParams.model_json_schema()}\n"
                "Be sure to pass `has=['photo']` when searching for images."
            ),
            "nullable": True,
        },
    }
    output_type = "string"

    def forward(self, text: str, extra_arguments: dict = {}):
        params = {"text": text}
        params.update(extra_arguments)
        raw_response = requests.post(
            f"{settings.squad_api_base_url}/data/x/search",
            json=params,
            headers={
                "Authorization": settings.authorization,
            },
        )
        tweets = raw_response.json()
        if tweets:
            response = ["Here are some tweets that may be of relevance:"]
            tweets = [Tweet.from_index(item) for item in raw_response.json()]
            for tweet in tweets:
                tweet_str = "\n".join(
                    [f"{key}: {value}" for key, value in tweet.model_dump().items()]
                )
                response.append(tweet_str)
                response.append(f"URL: https://x.com/i/status/{tweet.id}")
                response.append("---")
            return "\n".join(response)


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
        print(f"Trying to create a tweet:\n\t{text=}\n\t{media=}")
        if not settings.x_live_mode:
            return "Successfully tweeted: 234234"
        form_data = {
            "text": ("", text),
        }
        if in_reply_to:
            form_data["in_reply_to"] = ("", in_reply_to)
        if media:
            with open(media, "rb") as infile:
                file_bytes = infile.read()
                filename = os.path.basename(media)
                form_data["media"] = (filename, file_bytes)
        response = requests.post(
            f"{settings.squad_api_base_url}/x/tweet",
            files=form_data,
            headers={
                "Authorization": settings.authorization,
            },
        )
        response.raise_for_status()
        return f"Successfully tweeted: {response.text}"


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
        if not settings.x_live_mode:
            return f"Successfully followed {user_id=}"
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
        if not settings.x_live_mode:
            return f"Successfully liked {tweet_id=}"
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
        if not settings.x_live_mode:
            return f"Successfully retweeted {tweet_id=}"
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
        if not settings.x_live_mode:
            return f"Successfully quote tweeted {tweet_id=}"
        response = requests.post(
            f"{settings.squad_api_base_url}/x/like",
            json={"tweet_id": tweet_id, "text": text},
            headers={
                "Authorization": settings.authorization,
            },
        )
        response.raise_for_status()
        return f"Successfully quote tweeted {tweet_id=}: {response.text}"
