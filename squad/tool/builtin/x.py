import requests
from smolagents import Tool
from squad.data.schemas import XSearchParams
from squad.storage.x import Tweet
from squad.agent_config import settings


class XSearcher(Tool):
    name = "x_search"
    description = "Tool for performing searches on X (formerly twitter) to find information and media related to a topic."
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
        tweets = [Tweet.from_index(item) for item in raw_response.json()]
        response = []
        for tweet in tweets:
            tweet_str = "\n".join([f"{key}: {value}" for key, value in tweet.model_dump().items()])
            response.append(tweet_str)
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
            "description": "ID of the tweet/X post this is a reply to, if it is a reply",
            "nullable": True,
        },
        "image": {
            "type": "string",
            "nullable": True,
            "description": "Full path to an image to include as attachment in the post.",
        },
        "video": {
            "type": "string",
            "nullable": True,
            "description": "Full path to a video file to include as an attachment in the post.",
        },
    }
    output_type = "string"

    def forward(self, text: str, in_reply_to: str = None, image: str = None, video: str = None):
        print("TODO: create a tweet: {text=} {attachments=} {in_reply_to=}")


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
        print("TODO: follow user {user_id}")


class XLiker(Tool):
    name = "x_like"
    description = "Tool to 'like' a post on X/twitter."
    inputs = {
        "tweet_id": {
            "type": "string",
            "description": "ID of the tweet to like",
        },
    }
    output_type = "string"

    def forward(self, user_id: str):
        print("TODO: like tweet {tweet_id}")


class XRetweeter(Tool):
    name = "x_retweet"
    description = "Tool for re-tweeting a tweet, with no additional text/comment just a re-tweet."
    inputs = {
        "tweet_id": {
            "type": "string",
            "description": "ID of the tweet to re-tweet",
        },
    }
    output_type = "string"

    def forward(self, tweet_id: str):
        print("TODO: retweet {tweet_id}")


class XQuoteTweeter(Tool):
    name = "x_quote_tweet"
    description = "Tool for re-tweeting a tweet with additional comments/text."
    inputs = {
        "tweet_id": {
            "type": "string",
            "description": "ID of the tweet to re-tweet",
        },
        "text": {
            "type": "string",
            "description": "The text you want to add with the retweet, i.e. your comments on the original post",
        },
    }
    output_type = "string"

    def forward(self, tweet_id: str, text: str):
        print("TODO: quote tweet {tweet_id} with {text=}")
