import requests
import traceback
from smolagents import Tool
from smolagents.agents import TaskStep, SystemPromptStep
from smolagents.utils import parse_code_blobs
from smolagents.tools import AUTHORIZED_TYPES
from squad.agent_config import get_agent, settings
from squad.tool.prompts import TOOL_WRITING_PROMPT


class DangerousDynamo(Tool):
    name = "dynamic_tool_writer"
    description = (
        "This is a tool that dynamically creates more tools, if none of the existing tools are particularly well-suited for the task. "
        f"Functions created by this tool can ONLY use one of the following output_type values: {AUTHORIZED_TYPES}\n"
        "You can create any tool so long as it is fairly concise and pure python, not reliant on any authenticated applications, and do not require GPUs, e.g. math tools, web crawling tools, etc. "
        "You will NEVER try to call the tools after invoking dynamic_tool_writer! The return value is NOTHING, never assign the call to a variable. "
        "To use this function, simple invoke dynamic_tool_writer with the function you'd like written, then stop. "
        "You cannot install packages with pip, they must be built-in standard libraries, or Image from PIL. "
        "This tool is only capable of creating a single new tool, never try to call it twice in the same code block. "
        "Never write functions that would require a human in the loop or external credentials, etc."
    )
    inputs = {
        "task": {
            "type": "string",
            "description": "The task/instructions that the new tool should be able to handle.",
        },
    }
    output_type = "string"

    def forward(self, task: str):
        response = requests.post(
            "https://llm.chutes.ai/v1/chat/completions",
            headers={"Authorization": settings.authorization},
            json={
                "model": "deepseek-ai/DeepSeek-R1",
                "messages": [
                    {
                        "role": "user",
                        "content": TOOL_WRITING_PROMPT.replace("TASK", task),
                    }
                ],
                "temperature": 0,
                "max_tokens": 24000,
                "seed": 42,
            },
        )
        if response.status_code == 200:
            message = response.json()["choices"][0]["message"]["content"]
            code = parse_code_blobs(message)
            print(code)
            try:
                existing_tools = {name: cls for name, cls in globals().items()}
                exec(compile(code, "<string>", "exec"), globals())
                tool_added = False
                for name, cls in globals().items():
                    if (
                        isinstance(cls, type)
                        and issubclass(cls, Tool)
                        and name not in existing_tools
                    ):
                        print(f"Created a new tool: {name}")
                        agent = get_agent()
                        tool_instance = cls()
                        agent.tools[tool_instance.name] = tool_instance
                        agent.python_executor.custom_tools[tool_instance.name] = tool_instance
                        tool_added = True

                if tool_added:
                    return "__:NEW_TOOL_CREATED:__"
                return "No new tools were created."

            except Exception as exc:
                print(f"Failed to extract new tool: {exc}\n{traceback.format_exc()}")
                raise
        else:
            raise Exception(f"Failed to get LLM response: {response.status_code}")


def wipe_tool_creation_step(step):
    if not step.tool_calls:
        return
    if any(
        call.name == "python_interpreter" and "dynamic_tool_writer(" in call.arguments
        for call in step.tool_calls
    ):
        agent = get_agent()
        agent.system_prompt = agent.initialize_system_prompt()
        agent.logs = [
            SystemPromptStep(system_prompt=agent.system_prompt),
            TaskStep(task=agent.task),
        ]
        agent.monitor.reset()
