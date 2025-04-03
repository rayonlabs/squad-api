import os
import re
import wave
import requests
import tempfile
from smolagents import Tool
from squad.agent_config import settings


def merge_wavs(wav_files):
    if len(wav_files) == 1:
        return wav_files[0]
    with wave.open(wav_files[0], "rb") as first_wav:
        params = first_wav.getparams()
    with tempfile.NamedTemporaryFile(delete=False) as outfile:
        output_path = outfile.name
    with wave.open(output_path, "wb") as output_wav:
        output_wav.setparams(params)
        for i, wav_path in enumerate(wav_files):
            with wave.open(wav_path, "rb") as wav:
                output_wav.writeframes(wav.readframes(wav.getnframes()))
    return output_path


def split_text(text: str, max_length: int = 500) -> list[str]:
    """
    Recursively splits text into chunks based on different delimiters depending on length.
    """
    text = text.strip()
    if len(text) <= max_length:
        return [text]
    delimiters = [
        ("\n\n", "paragraphs"),
        ("\n", "single newlines"),
        (".", "sentences"),
        (";", "semicolons"),
        (",", "commas"),
        (" ", "spaces"),
        ("", "characters"),
    ]

    for delimiter, _ in delimiters:
        if not delimiter:
            chunks = [text[i : i + max_length] for i in range(0, len(text), max_length)]
            return chunks
        if delimiter in text:
            chunks = text.split(delimiter)
            result = []
            current_chunk = []
            current_length = 0
            for chunk in chunks:
                chunk = chunk.strip()
                if not chunk:
                    continue
                delimiter_len = len(delimiter) if delimiter != "\n\n" else 2
                chunk_length = len(chunk) + delimiter_len
                if current_length + chunk_length > max_length and current_chunk:
                    combined_chunk = delimiter.join(current_chunk)
                    result.extend(split_text(combined_chunk, max_length))
                    current_chunk = [chunk]
                    current_length = chunk_length
                else:
                    current_chunk.append(chunk)
                    current_length += chunk_length
            if current_chunk:
                combined_chunk = delimiter.join(current_chunk)
                result.extend(split_text(combined_chunk, max_length))
            return result
    return [text[i : i + max_length] for i in range(0, len(text), max_length)]


def tts_tool(
    voice: str = settings.default_tts_voice,
    slug: str = settings.default_tts_slug,
    tool_name: str = None,
    tool_description: str = None,
):
    """
    Helper to return dynamically created text-to-speech (TTS) tool classes.
    """

    if not tool_name:
        tool_name = "tts_" + re.sub(r"[^a-z0-9_]", "_", voice.lower())
        tool_name = re.sub("_+", "_", tool_name)
        tool_name = tool_name.rstrip("_")
        clazz_name = "TTS" + "".join(word.capitalize() for word in tool_name[4:].split("_"))
    else:
        clazz_name = "TTS" + "".join(word.capitalize() for word in tool_name.split("_"))
    if not tool_description:
        tool_description = (
            f"This is a tool that can call TTS with voice '{voice}' to perform text-to-speech, i.e. it can create speech audio data from text prompts. "
            "If asked to say something literally, use this tool. The output is the path on disk to the saved .wav audio file."
        )

    class DynamicTTSTool(Tool):
        name = tool_name
        description = tool_description
        inputs = {
            "text": {
                "type": "string",
                "description": "The text to read/synthesize audio from.",
            },
        }
        output_type = "string"

        def forward(self, text: str) -> str:
            nonlocal voice, slug
            chunks = split_text(text)
            outputs = []
            for chunk in chunks:
                preview = " ".join(chunk.splitlines())[0:100] + "..."
                print(f"Narrating: {preview}")
                response = requests.post(
                    f"https://{slug}.chutes.ai/speak",
                    json={
                        "text": chunk,
                        "voice": voice,
                    },
                    headers={
                        "Authorization": settings.authorization,
                    },
                )
                with tempfile.NamedTemporaryFile(mode="wb", suffix=".wav", delete=False) as outfile:
                    outfile.write(response.content)
                    outputs.append(outfile.name)

            # Combine the audio files into one.
            final_path = None
            try:
                final_path = merge_wavs(outputs)
                return final_path
            except Exception as exc:
                import traceback

                print(f"ERROR HERE: {exc}\n{traceback.format_exc()}")
            finally:
                for path in outputs:
                    if path != final_path:
                        os.remove(path)

    return type(clazz_name, (DynamicTTSTool,), {})
