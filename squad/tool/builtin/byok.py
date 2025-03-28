import re
from urllib.parse import urlparse
import pybase64 as base64
import requests
from typing import Any, Optional
from smolagents import Tool
from squad.agent_config import settings


def byok_tool(
    upstream_url: str,
    secret_name: str,
    method: str = "post",
    tool_name: Optional[str] = None,
    tool_description: Optional[str] = None,
):
    """
    Helper to return dynamically created "bring your own key" request tools.
    """
    hostname = urlparse(upstream_url).netloc
    if not tool_name:
        tool_name = "byok_" + re.sub(r"[^a-z0-9_]", "_", hostname.lower())
        tool_name = re.sub("_+", "_", tool_name)
        tool_name = tool_name.rstrip("_")
        clazz_name = "BYOKRequest" + "".join(word.capitalize() for word in tool_name[4:].split("_"))
    else:
        clazz_name = "BYOKRequest" + "".join(word.capitalize() for word in tool_name.split("_"))

    class DynamicBYOKRequestTool(Tool):
        name = tool_name
        description = tool_description or "Perform requests to {upstream_url} via {method}"
        inputs = {
            "params": {
                "type": "object",
                "description": "Query parameters (not part of request body, URL query params)",
                "nullable": True,
            },
            "body": {
                "type": "any",
                "description": "The request body to include in the request.",
                "nullable": True,
            },
            "headers": {
                "type": "object",
                "description": "Additional request headers to include in the request.",
                "nullable": True,
            },
        }
        output_type = "object"

        def forward(self, params: dict = None, body: Any = None, headers: dict = None) -> dict:
            nonlocal upstream_url, secret_name, method
            request_body = {
                "secret_name": secret_name,
                "method": method,
                "upstream_url": upstream_url,
                "headers": headers,
            }
            if isinstance(body, bytes):
                request_body["body"] = {
                    "type": "bytes",
                    "value": base64.b64encode(body).decode(),
                }
            elif isinstance(body, str):
                request_body["body"] = {
                    "type": "bytes",
                    "value": base64.b64encode(body.encode()).decode(),
                }
            elif isinstance(body, dict):
                request_body["body"] = {
                    "type": "json",
                    "value": body,
                }

            # Send it.
            result = requests.post(
                f"{settings.squad_api_base_url}/data/byok",
                json=request_body,
                headers={"Authorization": settings.authorization},
            )
            result.raise_for_status()
            return result.json()

    return type(clazz_name, (DynamicBYOKRequestTool,), {})
