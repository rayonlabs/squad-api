import os
import re
import uuid
from io import BytesIO
import pybase64 as base64
import requests
from PIL import Image
from typing import Any, Optional
from smolagents import Tool
from squad.agent_config import settings


def agent_caller_tool(
    agent: str,
    tool_description: str,
    tool_name: str = None,
    public: Optional[bool] = True,
):
    """
    Helper to return dynamically created Agent Caller tool classes.
    """

    if not tool_name:
        tool_name = "agent_caller_" + re.sub(r"[^a-z0-9_]", "_", agent.lower())
        tool_name = re.sub("_+", "_", tool_name)
        tool_name = tool_name.rstrip("_")
        clazz_name = "AgentCaller" + "".join(word.capitalize() for word in tool_name[4:].split("_"))
    else:
        clazz_name = "AgentCaller" + "".join(word.capitalize() for word in tool_name.split("_"))

    class DynamicAgentCallerTool(Tool):
        name = tool_name
        description = tool_description
        inputs = {
            "task": {
                "type": "string",
                "description": "The task the agent should perform.",
            },
            "inputs": {
                "type": "any",
                "description": "List of input files to provide the agent to accomplish the task, ideally provided as a list of strings to paths on disk.",
            },
        }
        output_type = "object"

        def forward(self, task: str, inputs: Any) -> dict:
            nonlocal agent, public
            if inputs:
                if not isinstance(inputs, list):
                    inputs = [inputs]

            # Build the request body, converting inputs to b64.
            body = {"task": task, "files_b64": []}
            for item in inputs:
                if isinstance(item, str):
                    if len(item) <= 1024 and os.path.exists(item):
                        with open(item, "rb") as infile:
                            body["files_b64"].append(
                                {
                                    os.path.basename(item): base64.b64encode(
                                        infile.read()
                                    ).decode(),
                                }
                            )
                    else:
                        body["files_b64"].append(
                            {
                                f"{uuid.uuid4()}.txt": base64.b64encode(item.encode()).decode(),
                            }
                        )
                elif isinstance(item, Image):
                    buffer = BytesIO()
                    item.save(buffer, format="JPEG")
                    buffer.seek(0)
                    body["files_64"].append(
                        {f"{uuid.uuid4()}.jpg": base64.b64encode(buffer.getvalue()).decode()}
                    )
                elif isinstance(item, bytes):
                    body["files_64"].append(
                        {f"{uuid.uuid4()}.bin": base64.b64encode(item).decode()}
                    )
                else:
                    raise ValueError(
                        f"Unsupported value passed in inputs: class {item.__class__}, must be path to file, PIL.Image, or bytes."
                    )

            # Create the invocation.
            result = requests.post(
                f"{settings.squad_api_base_url}/{agent}/invoke",
                json=body,
                headers={"Authorization": settings.authorization},
            )
            result.raise_for_status()
            invocation = result.json()
            invocation_id = invocation["invocation_id"]

            # Wait for it to complete.
            complete = False
            stream = requests.get(
                f"{settings.squad_api_base_url}/invocations/{invocation_id}/stream",
                stream=True,
                headers={"Authorization": settings.authorization},
            )
            try:
                for chunk in stream.iter_content():
                    if chunk:
                        chunk = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
                        if chunk.startswith("data") and chunk.strip() != "DONE":
                            print(chunk[6:])
            except Exception:
                ...
            invocation = None
            while not complete:
                try:
                    result = requests.get(
                        f"{settings.squad_api_base_url}/invocations/{invocation_id}",
                        headers={"Authorization": settings.authorization},
                    )
                    result.raise_for_status()
                    invocation = result.json()
                    if invocation.get("status") in ("success", "error"):
                        complete = True
                except Exception:
                    ...
            return invocation

    return type(clazz_name, (DynamicAgentCallerTool,), {})
