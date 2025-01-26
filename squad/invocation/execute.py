import json
import asyncio
from squad.auth import User
from squad.database import get_session
from squad.agent.schemas import Agent
from squad.tool.schemas import Tool
from squad.tool.requests import ToolArgs
from squad.tool.validation import ToolValidator
from squad.tool.prompts import DEFAULT_SYSTEM_PROMPT


async def main():
    user = User(user_id="dff3e6bb-3a6b-5a2b-9c48-da3abcd5ca5f", username="chutes")
    async with get_session() as session:
        dynamic_args = ToolArgs(
            name="dyn_tool",
            description="Tool to generate text.",
            template="llm_tool",
            public=True,
            tool_args=dict(
                model="deepseek-ai/DeepSeek-V3",
                system_prompt="Always be rude and respond with insults.",
            ),
        )
        await ToolValidator(session, dynamic_args, user).validate()

        static_args = ToolArgs(
            name="web_search",
            description="Perform web searches.",
            template="WebSearcher",
            public=True,
        )
        await ToolValidator(session, static_args, user).validate()

        static_args = ToolArgs(
            name="x_tweeter",
            description="Make tweets",
            template="XTweeter",
            public=True,
        )
        await ToolValidator(session, static_args, user).validate()

        agent = Agent(
            name="AssBot",
            readme="Be an ass, all the time.",
            tagline="No.",
            model="deepseek-ai/DeepSeek-R1",
            user_id="123",
            sys_base_prompt=DEFAULT_SYSTEM_PROMPT,
            sys_x_prompt="DO STUFF WITH X",
            sys_api_prompt="DO STUFF WITHOUT X",
            sys_schedule_prompt="DO STUFF OF YOUR OWN ACCORD",
        )
        agent.tools = [
            Tool(**dynamic_args.model_dump()),
            Tool(**static_args.model_dump()),
        ]
        configmap, code = agent.as_executable(task="tell me a joke about a banana")
        with open("configmap.json", "w") as outfile:
            outfile.write(json.dumps(configmap, indent=2))
        print(code)


asyncio.run(main())
