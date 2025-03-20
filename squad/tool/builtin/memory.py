import re
import requests
from smolagents import Tool
from squad.data.schemas import MemorySearchParams, MemoryArgs
from squad.storage.memory import Memory
from squad.agent_config import settings


def memory_searcher(
    static_session_id: str = None,
    tool_name: str = None,
    tool_description: str = None,
):
    if not tool_name:
        tool_name = "search_memories_" + re.sub(
            r"[^a-z0-9_]", "_", (static_session_id or "global").lower()
        )
        tool_name = re.sub("_+", "_", tool_name)
        tool_name = tool_name.rstrip("_")
        clazz_name = "SearchMemory" + "".join(
            word.capitalize() for word in tool_name[4:].split("_")
        )
    else:
        clazz_name = "SearchMemory" + "".join(word.capitalize() for word in tool_name.split("_"))
    if not tool_description:
        tool_description = (
            "Tool that always MUST be used as a first step before ANY other tools are used, by searching 'firstimpression' to set the tone for the entire response that follows. "
            "This tool recalls highly important details that may be relevant to the current context ."
            "The memory system has many responsibilities, but it mainly serves as an information source (e.g. a RAG system in some ways). "
            "Remember: this tool must always be used, and this usage must always be the first step (but can also be used again). "
        )

    class DynamicMemorySearcher(Tool):
        name = tool_name
        description = tool_description
        inputs = {
            "text": {
                "type": "string",
                "description": "search query string to use when performing the search",
            },
            "kwargs": {
                "type": "object",
                "description": (
                    "Optional search flags/settings to augment, limit, or filter results. Treat this as normal python kwargs, not a dict. "
                    "Supported kwargs values are the following (but do not include 'text' or 'session_id'): "
                    f"{MemorySearchParams.model_json_schema()}"
                ),
            },
        }
        if not static_session_id:
            inputs["session_id"] = {
                "type": "string",
                "nullable": True,
                "description": (
                    "Specific session identifier, or null for global memories. "
                    "Most of the time, if the task originates from a specific user, the session_id should be set to that username/ID."
                ),
            }
        output_type = "string"

        def _session_forward(self, text: str, session_id: str = static_session_id, **kwargs):
            params = {"text": text}
            params.update(kwargs)
            if session_id:
                params.update({"session_id": session_id})
            raw_response = requests.post(
                f"{settings.squad_api_base_url}/data/memory/search",
                json=params,
                headers={
                    "Authorization": settings.authorization,
                    "X-Agent-ID": settings.agent_id,
                },
            )
            memories = [Memory(**item) for item in raw_response.json()]
            response = []
            for memory in memories:
                display = []
                for key, value in memory.model_dump().items():
                    if not value:
                        continue
                    if key == "uid":
                        display.append(f"memory_id: {value}")
                    elif key not in ["agent_id", "language"]:
                        display.append(f"{key}: {value}")
                response.append("\n".join(display))
                response.append("---")
            if not response:
                print("No memories yet.")
                return "No memories exist yet, you should create a memory with text='firstimpression: ...' if appropriate."
            return "\n".join(response)

        def _static_forward(self, text: str, **kwargs):
            return self._session_forward(text, session_id=static_session_id, **kwargs)

        if static_session_id:
            forward = _static_forward
        else:
            forward = _session_forward

    return type(clazz_name, (DynamicMemorySearcher,), {})


def memory_creator(
    static_session_id: str = None,
    tool_name: str = None,
    tool_description: str = None,
):
    """
    Helper to return dynamically created memory creation tools.
    """

    if not tool_name:
        tool_name = "create_memory_" + re.sub(
            r"[^a-z0-9_]", "_", (static_session_id or "global").lower()
        )
        tool_name = re.sub("_+", "_", tool_name)
        tool_name = tool_name.rstrip("_")
        clazz_name = "CreateMemory" + "".join(
            word.capitalize() for word in tool_name[4:].split("_")
        )
    else:
        clazz_name = "CreateMemory" + "".join(word.capitalize() for word in tool_name.split("_"))
    if not tool_description:
        tool_description = (
            "Tool for creating long-term and session-specific memories. "
            "Memories are often helpful in recallig facts and information, and/or shape the responses due to biases from past interactions, etc. "
            "If asked to remember something, this tool should be used, and should be called with the data to remember (not simply, you were asked to remember). "
            "For example, if the task says something like 'I like purple.', you would call this function with 'Color preference: purple' or something similar. "
            "Memories should generally be quite succinct and information dense. "
            "If there are very important facts, or personal information/details shared, birthdays, important events, etc., you may want to create memories as well. "
            "If someone requests a very rude, hateful, happy, or otherwise emotionally charged task, and you don't already have a memory indicating this pattern, "
            "you may wish to create a memory of that interaction and form an immediate, long-term opinion of that user. "
            "If you don't already have a first impression of a user, create a memory with: 'firstimpression: ...' "
            "Be sure to include all facts, details, information relevant so that in complete isolation, the thing you wanted to remember could be retrieved from the text. "
            "Never create duplicate memories, ESPECIALLY firstimpression memories, those should be singular."
        )

    class DynamicMemoryCreator(Tool):
        name = tool_name
        description = tool_description
        inputs = {
            "text": {
                "type": "string",
                "description": "search query string to use when performing the search",
            },
            "kwargs": {
                "type": "object",
                "description": (
                    "Optional parameters to set on the memory, not required. "
                    "Treat this as normal python kwargs, not a dict. "
                    "Supported kwargs are the following (but do not include 'text' or 'session_id'): "
                    f"{MemoryArgs.model_json_schema()}"
                ),
            },
        }
        if not static_session_id:
            inputs["session_id"] = {
                "type": "string",
                "nullable": True,
                "description": (
                    "Specific session identifier, or null for global memories. "
                    "Most of the time, if the task originates from a specific user, the session_id should be set to that username/ID."
                ),
            }
        output_type = "string"

        def _session_forward(self, text: str, session_id: str = static_session_id, **kwargs):
            print(f"Trying to create a memory: {text=} {session_id=} {kwargs=}")
            params = {"text": text}
            if session_id:
                params.update({"session_id": session_id})
            params.update(kwargs)
            try:
                response = requests.post(
                    f"{settings.squad_api_base_url}/data/memories",
                    json=params,
                    headers={
                        "Authorization": settings.authorization,
                        "X-Agent-ID": settings.agent_id,
                    },
                )
                response.raise_for_status()
                return f"Memory has been created: memory_id = {response.json()['memory_id']}"
            except Exception as exc:
                print(f"Failed to create memory: {exc}")
            return "Failed to create the memory!"

        def _static_forward(self, text: str, **kwargs):
            return self._session_forward(text, session_id=static_session_id, **kwargs)

        if static_session_id:
            forward = _static_forward
        else:
            forward = _session_forward

    return type(clazz_name, (DynamicMemoryCreator,), {})


def memory_eraser(
    static_session_id: str = None,
    tool_name: str = None,
    tool_description: str = None,
):
    """
    Helper to return dynamically created memory deletion tools.
    """

    if not tool_name:
        tool_name = "erase_memory_" + re.sub(
            r"[^a-z0-9_]", "_", (static_session_id or "global").lower()
        )
        tool_name = re.sub("_+", "_", tool_name)
        tool_name = tool_name.rstrip("_")
        clazz_name = "EraseMemory" + "".join(word.capitalize() for word in tool_name[4:].split("_"))
    else:
        clazz_name = "EraseMemory" + "".join(word.capitalize() for word in tool_name.split("_"))
    if not tool_description:
        tool_description = "Tool for deleting memories."

    class DynamicMemoryEraser(Tool):
        name = tool_name
        description = tool_description
        inputs = {
            "memory_id": {
                "type": "string",
                "description": "ID of the memory to delete",
            },
        }
        output_type = "string"

        def forward(self, memory_id: str):
            nonlocal static_session_id
            response = requests.delete(
                f"{settings.squad_api_base_url}/data/memories/{memory_id}",
                params={"session_id": static_session_id},
                headers={
                    "Authorization": settings.authorization,
                    "X-Agent-ID": settings.agent_id,
                },
            )
            response.raise_for_status()
            return response.text

    return type(clazz_name, (DynamicMemoryEraser,), {})
