"""
Apex Search (sn1) tools.
"""

import requests
import asyncio
from typing import Optional
from smolagents import Tool
from squad.util import rerank
from squad.agent_config import settings


class ApexWebSearcher(Tool):
    name = "apex_web_search"
    description = "Tool for performing web searches via Apex Search. These web searches return the most relevant search results along with the context of the relevant bits."
    inputs = {
        "query": {
            "type": "string",
            "description": "Query string, i.e. the search term/question/phase to perform a web search with.",
        },
        "miners": {
            "type": "integer",
            "nullable": True,
            "default": 5,
            "description": "Number of unique miners to query, which can increase diversity of searches and recall.",
        },
        "limit": {
            "type": "integer",
            "nullable": True,
            "default": 10,
            "description": "Maximum number of results (up to 20).",
        },
        "top_n": {
            "type": "integer",
            "nullable": True,
            "description": "Rerank the search results via an embedding rerank algorithm to return only the top N results.",
        },
        "timeout": {
            "type": "integer",
            "nullable": True,
            "default": 15,
            "description": "Query timeout in seconds.",
        },
    }

    def forward(
        self,
        query: str,
        miners: Optional[int] = 5,
        limit: Optional[int] = 10,
        top_n: Optional[int] = None,
        timeout: Optional[int] = 15,
    ):
        params = {
            key: value
            for key, value in {
                "query": query,
                "miners": miners,
                "limit": limit,
                "timeout": timeout,
            }.items()
            if value is not None
        }
        raw_response = requests.post(
            f"{settings.squad_api_base_url}/data/apex/web_search",
            json=params,
            headers={
                "Authorization": settings.authorization,
            },
        )
        search_results = raw_response.json()["data"]
        keys_to_keep = ["url", "relevant", "content"]
        if search_results:
            singular_items = []
            for result in search_results:
                singular_items.append(
                    "\n".join(
                        [f"{key}: {value}" for key, value in result.items() if key in keys_to_keep]
                    )
                )
            return_docs = singular_items
            if top_n:
                loop = asyncio.get_event_loop()
                return_docs = loop.run_until_complete(
                    rerank(query, singular_items, top_n=top_n, auth=settings.authorization)
                )
            return "\n---\n".join(return_docs)
