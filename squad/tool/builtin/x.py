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
