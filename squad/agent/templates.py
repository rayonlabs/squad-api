DEFAULT_IMPORTS = """
import os
import json
import asyncio
import base64
import tempfile
from smolagents import CodeAgent, OpenAIServerModel
from squad.agent_config import settings
from squad.agent_config import get_agent, set_agent
from smolagents.local_python_executor import BASE_PYTHON_TOOLS
BASE_PYTHON_TOOLS["open"] = open
tempfile.tempdir = "/tmp/outputs"
"""

MAIN_TEMPLATE = """
settings.authorization = __tool_args["authorization"]
class _SafeSerializer(json.JSONEncoder):
    def default(self, obj):
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)
def _execution_step_logger(step):
    try:
        with open("/tmp/outputs/_steps.log", "a+") as outfile:
            outfile.write(str(step) + "\\n\\n")
    except Exception:
        ...
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
    step_callbacks=[_execution_step_logger] + __tool_args["agent_callbacks"],
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
final_answer = agent.run(__tool_args["task"])
with open("/tmp/outputs/_final_answer.json", "w") as outfile:
    outfile.write(json.dumps(final_answer, cls=_SafeSerializer, indent=2))
"""
