import re
import openai
from smolagents import Tool
from squad.agent_config import settings


def llm_tool(
    model: str = settings.default_text_gen_model,
    tool_name: str = None,
    tool_description: str = None,
    endpoint: str = "chat",
    system_prompt: str = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    **kwargs,
):
    """
    Helper to return dynamically created LLM tool classes.
    """

    if not tool_name:
        tool_name = "llm_" + re.sub(r"[^a-z0-9_]", "_", model.lower())
        tool_name = re.sub("_+", "_", tool_name)
        tool_name = tool_name.rstrip("_")
        clazz_name = "LLM" + "".join(word.capitalize() for word in tool_name[4:].split("_"))
    else:
        clazz_name = "LLM" + "".join(word.capitalize() for word in tool_name.split("_"))
    if not tool_description:
        tool_description = (
            f"This is a tool that can call LLM {model} to generate text output from text prompts."
        )

    class DynamicLLMTool(Tool):
        name = tool_name
        description = tool_description
        inputs = {
            "prompt": {
                "type": "string",
                "description": "The prompt to generate text/invoke the LLM with.",
            },
        }
        output_type = "string"

        def forward(self, prompt: str) -> str:
            nonlocal model, endpoint, system_prompt, kwargs
            call_args = {
                **{
                    "model": model,
                    "temperature": temperature,
                },
                **kwargs,
            }
            if endpoint == "chat":
                call_args["messages"] = []
                if system_prompt:
                    call_args["messages"].append(
                        {
                            "role": "system",
                            "content": system_prompt,
                        }
                    )
                call_args["messages"].append(
                    {
                        "role": "user",
                        "content": prompt,
                    }
                )
            else:
                call_args["prompt"] = prompt
            client = openai.OpenAI(
                base_url="https://llm.chutes.ai/v1",
                api_key=settings.authorization,
            )
            method = client.chat.completions if endpoint == "chat" else client.completions
            result = method.create(**call_args)
            return (
                result.choices[0].message.content if endpoint == "chat" else result.choices[0].text
            )

    return type(clazz_name, (DynamicLLMTool,), {})
