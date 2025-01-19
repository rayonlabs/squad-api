"""
Agent specific settings, only used in execution context.
"""

import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    authorization: str = os.getenv("CHUTES_API_TOKEN")
    default_image_model: str = os.getenv("DEFAULT_IMAGE_MODEL", "FLUX.1-schnell")
    default_text_gen_model: str = os.getenv(
        "DEFAULT_TEXT_GEN_MODEL", "nvidia/Llama-3.1-405B-Instruct-FP8"
    )


settings = Settings()
