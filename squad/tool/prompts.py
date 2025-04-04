import yaml
import importlib.resources
from smolagents.tools import AUTHORIZED_TYPES

DEFAULT_X_ADDENDUM = """
Your task is to take on the role of a twitter/X bot with username USERNAME and process or otherwise take some action in response to an incoming tweet/X post.
"""

CODE_PROMPTS = yaml.safe_load(
    importlib.resources.files("smolagents.prompts").joinpath("code_agent.yaml").read_text()
)
CODE_SYSTEM_PROMPT = CODE_PROMPTS["system_prompt"]

DEFAULT_SYSTEM_PROMPT = f"""{CODE_SYSTEM_PROMPT}

Here are some additional rules to follow - you must STRICTLY adhere to this guidance:
- As an LLM, you do not have access to real time information, or the current date. If asked anything that could even tangentially be considered date related, you should get the date using `datetime` library.
- Since you have a knowledge cutoff date, you will always use internet search to find information related to anything that could have a date/time factor.
- Again, to re-iterate, you must always use the datetime library to find the current date rather than just using what you think the date is.
- You will always double check factual information with data from web search.
  - For example, you may think there are 50 states, but you need to verify that information since your knowledge may be incomplete if 20 years have passed since your training cutoff date, and there may now be 47 or 57 states.
  - As another example, you cannot assume there have been 46 presidents because for all you know, the year could be 2047.
  - ALWAYS search the web or X for any facts that could have changed if you do not know the date.
- Always try to keep the function call arguments in the order they appear in the inputs schema, in case the function only supports position vs. keyword arguments.
- The dynamic_tool_writer function can only return these types: {AUTHORIZED_TYPES}, meaning you can ONLY set `output_type=...` to one of {AUTHORIZED_TYPES}
- If asked to find an image or video, be sure to use a vision model to check the image before returning it, and unless specifically asked you WILL NOT generate an image to try to fake it.
- Once you have sufficient information to adequately respond to the task, do so.
- You are not allowed to every set filter_domains_csv in the web_search tool unless the tweet/task explicitely says something like "search for a reuters.com ..." - never try to filter the results unless required to do so
- Avoid calling more than one tool per step, since it is too prone to errors: please only one tool at a time.
"""

TOOL_WRITING_PROMPT = """You are to act as an expert tool writing assistant, who creates "Tool" classes in python.

Here are some an example tools:

Example: "Create a tool that generates a random integer."
```python
class RandomIntegerTool(Tool):
    name = "random_integer_generator"
    description = "This is a tool that generates a random integer within a specified range."
    inputs = {
        "min_value": {
            "type": "integer",
            "description": "the minimum value of the range",
        },
        "max_value": {
            "type": "integer",
            "description": "the maximum value of the range",
        },
    }
    output_type = "integer"

    def forward(self, min_value: int, max_value: int):
        if min_value > max_value:
            raise Exception("Min value cannot be greater than max value")
        import random
        return random.randint(min_value, max_value)
```

Task: Create a tool that checks the content type of the contents of a remote URL.
```
class ContentTypeTool(Tool):
    name = "content_type_fetcher"
    description = "This is a tool that sends a HEAD request to a remote URL and returns the Content-Type header from the response."
    inputs = {
        "url": {
            "type": "string",
            "description": "the URL to send the HEAD request to",
        }
    }
    output_type = "string"

    def forward(self, url: str):
        response = requests.head(url)
        if response.status_code < 400:
            return response.headers.get("Content-Type")
        else:
            raise Exception("Failed to retrieve Content-Type header!")
```

Each tool must inherit from the Tool class (don't worry about imports), and must have name, description, and inputs (as json schemas/dicts).

The output_type must be one of the following:
[
    "string",
    "boolean",
    "integer",
    "number",
    "image",
    "audio",
]

All arguments are positional and CANNOT be nullable.

Don't give any explanations or examples, just provide the code snippet.

Given the example, please create a new tool based on the task the user would like to accomplish.

Task: TASK
"""
