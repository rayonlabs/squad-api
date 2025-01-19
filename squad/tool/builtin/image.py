import re
import io
import requests
from squad.agent_config import settings
from PIL import Image
from smolagents import Tool


def image_tool(
    model: str = settings.default_image_model,
    tool_name: str = None,
    tool_description: str = None,
    height: int = 1024,
    width: int = 1024,
    num_inference_steps: int = 25,
    guidance_scale: float = 7.5,
    seed: int = 42,
    **kwargs,
):
    """
    Helper to return dynamically created image generation tool classes.
    """

    if not tool_name:
        tool_name = "img_" + re.sub(r"[^a-z0-9_]", "_", model.lower())
        tool_name = re.sub("_+", "_", tool_name)
        tool_name = tool_name.rstrip("_")
        clazz_name = "IMG" + "".join(word.capitalize() for word in tool_name[4:].split("_"))
    else:
        clazz_name = "IMG" + "".join(word.capitalize() for word in tool_name.split("_"))
    if not tool_description:
        tool_description = (
            f"This is a tool that can generate images with {model} from text prompts."
        )

    class DynamicImageTool(Tool):
        name = tool_name
        description = tool_description
        inputs = {
            "prompt": {
                "type": "string",
                "description": "The prompt to generate the image with.",
            },
        }
        output_type = "image"

        def forward(self, prompt: str) -> str:
            nonlocal model, height, width, num_inference_steps, guidance_scale, seed, kwargs
            result = requests.post(
                "https://image.chutes.ai/generate",
                json={
                    **{
                        "model": model,
                        "prompt": prompt,
                        "width": width,
                        "height": height,
                        "num_inference_steps": num_inference_steps,
                        "guidance_scale": guidance_scale,
                        "seed": seed,
                    },
                    **kwargs,
                },
                headers={
                    "Authorization": settings.authorization,
                },
            )
            result.raise_for_status()
            return Image.open(io.BytesIO(result.content))

    return type(clazz_name, (DynamicImageTool,), {})
