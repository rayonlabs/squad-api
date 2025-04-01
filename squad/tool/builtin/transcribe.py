import os
import pybase64 as base64
import requests
from smolagents import Tool
from squad.agent_config import settings


class TranscribeTool(Tool):
    name = "transcribe"
    description = "Tool to transcribe audio and optionally translate to another language."
    inputs = {
        "audio": {
            "type": "string",
            "description": "Audio to transcribe, which can be either a base64 encoded string containing the raw audio bytes, or a path to an audio file to transcribe.",
        },
        "language": {
            "nullable": True,
            "default": None,
            "type": "string",
            "description": "Target output language for the transcribed text, should only set if you do not want the text to be the same language as detected audio language (omit if english).",
        },
    }
    output_type = "string"

    def forward(self, audio: str, language: str = None) -> str:
        audio_b64 = audio
        if len(audio) <= 1024 and os.path.exists(audio):
            with open(audio, "rb") as infile:
                audio_b64 = base64.b64encode(infile.read()).decode()
        payload = {"audio_b64": audio_b64}
        if language:
            payload["language"] = language
        response = requests.post(
            "https://chutes-whisper-large-v3.chutes.ai/transcribe",
            json=payload,
            headers={
                "Authorization": settings.authorization,
            },
        )
        result = []
        for chunk in response.json():
            result.append(f"[{chunk['start']}-{chunk['end']}]: {chunk['text']}")
        return "\n".join(result)
