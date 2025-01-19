import re
import os
import openai
from smolagents import Tool

DEFAULT_TEXT_GEN_MODEL = os.getenv("DEFAULT_TEXT_GEN_MODEL", "nvidia/Llama-3.1-405B-Instruct-FP8")
CHUTES_API_KEY = os.getenv("CHUTES_API_KEY")


def llm_tool(
    model: str = DEFAULT_TEXT_GEN_MODEL,
    endpoint: str = "chat",
    system_prompt: str = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    **kwargs,
):
    """
    Helper to return dynamically created LLM tool classes.
    """

    function_name = "llm_" + re.sub(r"[^a-z0-9_]", "_", model.lower())
    function_name = re.sub("_+", "_", function_name)
    function_name = function_name.rstrip("_")
    clazz_name = "LLM" + "".join(word.capitalize() for word in function_name[4:].split("_"))

    class DynamicLLMTool(Tool):
        name = function_name
        description = f"This is a tool that can call LLM {model} to generate text output."
        inputs = {
            "prompt": {
                "type": "string",
                "description": "The prompt generate text/invoke the LLM with.",
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
                api_key=CHUTES_API_KEY,
            )
            method = client.chat.completions if endpoint == "chat" else client.completions
            result = method.create(**call_args)
            return (
                result.choices[0].message.content if endpoint == "chat" else result.choices[0].text
            )

    return type(clazz_name, (DynamicLLMTool,), {})
