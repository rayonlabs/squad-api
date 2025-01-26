"""
Agent specific settings, only used in execution context.
"""

import os
from typing import Optional
from pydantic_settings import BaseSettings

_AGENT_INSTANCE = None


def get_agent():
    return _AGENT_INSTANCE


def set_agent(agent):
    global _AGENT_INSTANCE
    _AGENT_INSTANCE = agent


class Settings(BaseSettings):
    agent_id: str = os.getenv("AGENT_ID", "test_agent")
    request_id: str = os.getenv("REQUEST_ID", "x")
    authorization: Optional[str] = os.getenv("CHUTES_API_TOKEN")
    default_image_model: str = os.getenv("DEFAULT_IMAGE_MODEL", "FLUX.1-schnell")
    default_vlm_model: str = os.getenv("DEFAULT_VLM_MODEL", "OpenGVLab/InternVL2_5-78B")
    default_text_gen_model: str = os.getenv("DEFAULT_TEXT_GEN_MODEL", "deepseek-ai/DeepSeek-R1")
    default_tts_voice: str = os.getenv("DEFAULT_TTS_VOICE", "af_sky")
    default_tts_slug: str = os.getenv("DEFAULT_TTS_SLUG", "chutes-kokoro-82m")
    squad_api_base_url: str = os.getenv("SQUAD_API_BASE_URL", "http://127.0.0.1:8000")
    x_live_mode: bool = os.getenv("X_LIVE_MODE", "false") == "true"


settings = Settings()
