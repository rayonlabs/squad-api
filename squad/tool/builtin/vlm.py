import io
import re
import pybase64 as base64
import openai
import requests
from PIL import Image
from typing import Any
from smolagents import Tool
from squad.agent_config import settings


def vlm_tool(
    model: str = settings.default_vlm_model,
    tool_name: str = None,
    tool_description: str = None,
    system_prompt: str = None,
    temperature: float = 0.7,
    max_images: int = 1,
    **kwargs,
):
    """
    Helper to return dynamically created VLM tool classes.
    """

    if not tool_name:
        tool_name = "vlm_" + re.sub(r"[^a-z0-9_]", "_", model.lower())
        tool_name = re.sub("_+", "_", tool_name)
        tool_name = tool_name.rstrip("_")
        clazz_name = "VLM" + "".join(word.capitalize() for word in tool_name[4:].split("_"))
    else:
        clazz_name = "VLM" + "".join(word.capitalize() for word in tool_name.split("_"))
    if not tool_description:
        tool_description = (
            f"This is a tool that can call VLM {model} to generate text output from a prompt and one or more images. "
            "Due to privacy concerns, these VLMs CANNOT identify or speculate on the identity of specific people. "
            "You WILL NOT EVER USE THIS TOOL TO IDENTIFY SPECIFIC PEOPLE."
        )

    class DynamicVLMTool(Tool):
        name = tool_name
        description = tool_description
        inputs = {
            "images": {
                "type": "any",
                "description": f"Image or list of images to include in function call. Each image can be a path to local file, PIL.Image object, or URL to remote image. Maximum number of images is {max_images}",
            },
            "prompt": {
                "type": "string",
                "description": "The prompt to generate text/invoke the VLM with.",
            },
        }
        output_type = "string"

        def forward(self, images: Any, prompt: str) -> str:
            nonlocal model, system_prompt, kwargs
            kwargs.pop("endpoint", None)
            if not isinstance(images, list):
                images = [images]
            images = images[:max_images]
            image_b64s = []
            for image in images:
                if not isinstance(image, Image.Image):
                    if not isinstance(image, str):
                        raise ValueError("Invalid image, must be str or Image type")
                    if image.startswith(("http:", "https:")):
                        try:
                            image = Image.open(io.BytesIO(requests.get(image).content))
                        except Exception:
                            print(f"Could not fetch {image=}")
                            image = None
                    else:
                        image = Image.open(image)
                if not image:
                    continue
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG")
                image_b64s.append(base64.b64encode(buffer.getvalue()).decode())
            if not image_b64s:
                return "No images to examine."

            call_args = {
                **{
                    "model": model,
                    "temperature": temperature,
                    "messages": [],
                    "stop": [
                        "<|im_end|>",
                        "<|end_of_text|>",
                        "</s>",
                    ],
                },
                **kwargs,
            }
            for key, value in call_args.items():
                if value is None:
                    call_args.pop(key)
            if system_prompt:
                call_args["messages"] = [
                    {
                        "role": "system",
                        "content": system_prompt,
                    }
                ] + call_args["messages"]
            call_args["messages"].append(
                {
                    "role": "user",
                    "content": [prompt]
                    + [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                            },
                        }
                        for b64 in image_b64s
                    ],
                }
            )
            client = openai.OpenAI(
                base_url="https://llm.chutes.ai/v1",
                api_key=settings.authorization,
            )
            result = client.chat.completions.create(**call_args)
            return result.choices[0].message.content

    return type(clazz_name, (DynamicVLMTool,), {})
