import os
import re
import base64
import openai
import requests
import subprocess
import glob
import tempfile
from smolagents import Tool
from squad.agent_config import settings


def split_video(input_file, clip_duration=30):
    """
    Split a video into segments.
    """
    duration_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        input_file,
    ]
    duration = float(subprocess.check_output(duration_cmd).decode().strip())
    if duration <= clip_duration:
        yield {"index": 0, "start": 0, "end": duration, "path": input_file}
        return

    with tempfile.TemporaryDirectory() as tempdir:
        ffmpeg_cmd = [
            "ffmpeg",
            "-i",
            input_file,
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(clip_duration),
            "-reset_timestamps",
            "1",
            "-map",
            "0",
            os.path.join(tempdir, "clip_%03d.mp4"),
        ]
        subprocess.run(ffmpeg_cmd)
        for path in sorted(glob.glob(os.path.join(tempdir, "clip*.mp4"))):
            index = int(path.split("_")[-1].split(".mp4")[0])
            yield {
                "index": index,
                "start": index * clip_duration,
                "end": index + clip_duration if index + clip_duration <= duration else duration,
                "path": path,
            }


def vvlm_tool(
    model: str = settings.default_vvlm_model,
    tool_name: str = None,
    tool_description: str = None,
    system_prompt: str = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    clip_duration: int = 30,
    **kwargs,
):
    """
    Helper to return dynamically created VLM tool classes, but for video specifically.
    """

    if not tool_name:
        tool_name = "vvlm_" + re.sub(r"[^a-z0-9_]", "_", model.lower())
        tool_name = re.sub("_+", "_", tool_name)
        tool_name = tool_name.rstrip("_")
        clazz_name = "VVLM" + "".join(word.capitalize() for word in tool_name[4:].split("_"))
    else:
        clazz_name = "VVLM" + "".join(word.capitalize() for word in tool_name.split("_"))
    if not tool_description:
        tool_description = f"This is a tool that can call VLM {model} to generate text output from a prompt and one or more images."

    class DynamicVVLMTool(Tool):
        name = tool_name
        description = tool_description
        inputs = {
            "video": {
                "type": "string",
                "description": "Video to include in the function call. This parameter should be a path to local file or a URL containing the video.",
            },
            "prompt": {
                "type": "string",
                "description": "The prompt to generate text/invoke the VLM with.",
            },
        }
        output_type = "string"

        def forward(self, video: str, prompt: str) -> str:
            nonlocal model, system_prompt, kwargs, clip_duration
            temp_path = None
            input_path = None
            with tempfile.NamedTemporaryFile(delete=False) as outfile:
                temp_path = outfile.name
            try:
                with open(temp_path, "wb") as outfile:
                    if isinstance(video, bytes):
                        outfile.write(video)
                        input_path = temp_path
                    elif isinstance(video, str):
                        if video.startswith(("http:", "https:")):
                            response = requests.get(video, stream=True)
                            response.raise_for_status()
                            for chunk in response.iter_content(chunk_size=8192):
                                outfile.write(chunk)
                            input_path = temp_path
                        else:
                            input_path = video
            finally:
                os.remove(temp_path)

            # Split into chunks.
            for clip in split_video(input_path, clip_duration):
                print(clip)
                with open(clip["path"], "rb") as infile:
                    b64 = base64.b64encode(infile.read()).decode()
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
                            "content": [
                                prompt,
                                {
                                    "type": "video_url",
                                    "video_url": {
                                        "url": f"data:video/mp4;base64,{b64}",
                                    },
                                },
                            ],
                        }
                    )
                    client = openai.OpenAI(
                        base_url="https://llm.chutes.ai/v1",
                        api_key=settings.authorization,
                    )
                    result = client.chat.completions.create(**call_args)
                    return result.choices[0].message.content

    return type(clazz_name, (DynamicVVLMTool,), {})
