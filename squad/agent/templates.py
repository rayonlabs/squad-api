DEFAULT_IMPORTS = """
import os
import json
import asyncio
import base64
from smolagents import CodeAgent, OpenAIServerModel
from squad.agent_config import settings
from squad.agent_config import get_agent, set_agent
"""

MAIN_TEMPLATE = """
settings.authorization = __tool_args["authorization"]
agent = CodeAgent(
    system_prompt=__tool_args["system_prompt"],
    additional_authorized_imports=[
        "PIL",
        "requests",
        "io",
        "asyncio",
        "playwright",
        "numpy",
        "np",
        "pandas",
        "pd",
        "sklearn",
        "pytz",
        "bs4",
        "matplotlib",
        "seaborn",
        "statsmodels",
        "plotly",
        "altair",
        "folium",
        "scipy",
        "sympy",
        "cv2",
        "pdf2image",
        "exifread",
        "rawpy",
        "openpyxl",
        "xlrd",
        "yaml",
        "csvkit",
        "PyPDF2",
        "lxml",
        "ujson",
        "orjson",
        "py7zr",
        "rarfile",
        "msgpack",
        "protobuf",
        "wandb",
        "pydub",
        "soundfile",
        "ffmpeg",
        "cairo",
        "pygraphviz",
        "pythreejs",
        "vtk",
        "pytesseract",
        "own",
        "markitdown",
    ],
    step_callbacks=__tool_args["agent_callbacks"],
    max_steps=__tool_args["max_steps"],
    tools=[{tool_name_str}],
    model=OpenAIServerModel(
        model_id=__tool_args["agent_model"],
        api_base="https://llm.chutes.ai/v1",
        api_key=settings.authorization,
    )
)
set_agent(agent)
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
agent.run(__tool_args["task"])
"""
