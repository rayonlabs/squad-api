DEFAULT_IMPORTS = """
import json
import asyncio
from smolagents import CodeAgent, OpenAIServerModel
from squad.agent_config import settings
from squad.agent_config import get_agent, set_agent
"""

MAIN_TEMPLATE = """
agent = CodeAgent(
    system_prompt=__tool_args["sys_base_prompt"],
    additional_authorized_imports=["PIL", "requests", "io"],
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
