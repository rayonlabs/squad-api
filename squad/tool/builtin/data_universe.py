"""
Data Universe (sn13) tools.
"""

import requests
import asyncio
from smolagents import Tool
from squad.util import rerank
from squad.agent_config import settings


class DataUniverseSearcher(Tool):
    name = "data_universe_search"
    description = "Tool for performing searches on Data Universe (sn13 from macrocosmos.ai) which includes X and reddit datasources."
    inputs = {
        "source": {
            "type": "string",
            "default": "x",
            "nullable": True,
            "description": "Data source identifier, either 'x' or 'reddit'",
        },
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": "list of keywords to search, between 1 and 5 items",
        },
        "usernames": {
            "type": "array",
            "items": {"type": "string"},
            "description": "optional list of usernames to search, between 0 and 5 items, each must be prefixed with '@', e.g. @username",
            "nullable": True,
        },
        "limit": {
            "type": "integer",
            "nullable": True,
            "default": 100,
        },
        "top_n": {
            "type": "integer",
            "nullable": True,
            "description": "perform reranking to return only the top top_n related tweets via an embedding reranking model",
        },
        "start_date": {
            "type": "string",
            "nullable": True,
            "description": "Date string in ISO 8601 format (YYYY-MM-DD) to filter results >=",
        },
        "end_date": {
            "type": "string",
            "nullable": True,
            "description": "Date string in ISO 8601 format (YYYY-MM-DD) to filter results <=",
        },
    }

    def forward(
        self,
        keywords: list[str],
        source: str = "x",
        usernames: list[str] = None,
        limit: int = 100,
        top_n: int = 10,
        start_date: str = None,
        end_date: str = None,
    ):
        params = {
            key: value
            for key, value in {
                "keywords": keywords,
                "source": source,
                "usernames": usernames,
                "limit": limit,
                "top_n": top_n,
                "start_date": start_date,
                "end_date": end_date,
            }.items()
            if value is not None
        }
        raw_response = requests.post(
            f"{settings.squad_api_base_url}/data/data_universe/search",
            json=params,
            headers={
                "Authorization": settings.authorization,
            },
        )
        search_results = raw_response.json()["data"]
        keys_to_keep = ["uri", "datetime", "source", "label", "content"]
        if search_results:
            singular_items = []
            for result in search_results:
                result["content"] = result["content"]["content"]
                singular_items.append(
                    "\n".join(
                        [f"{key}: {value}" for key, value in result.items() if key in keys_to_keep]
                    )
                )
            return_docs = singular_items
            if top_n:
                loop = asyncio.get_event_loop()
                return_docs = loop.run_until_complete(
                    rerank(
                        " ".join(keywords), singular_items, top_n=top_n, auth=settings.authorization
                    )
                )
            return "\n---\n".join(return_docs)
