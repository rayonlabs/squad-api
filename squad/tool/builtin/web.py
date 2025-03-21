import io
import re
import asyncio
import requests
import tempfile
from contextlib import contextmanager
from bs4 import BeautifulSoup
from PIL import Image
from playwright.sync_api import sync_playwright
from markdownify import markdownify
from smolagents import Tool
from squad.util import rerank
from squad.agent_config import settings
from squad.data.schemas import BraveSearchParams


@contextmanager
def get_browser(user_agent: str = None, viewport: dict[str, int] = None):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-http2"])
        context = browser.new_context(
            user_agent=user_agent
            or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport=viewport or {"width": 1920, "height": 1080},
            java_script_enabled=True,
            has_touch=True,
            locale="en-US",
            timezone_id="America/New_York",
        )
        page = context.new_page()
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => false});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        """)
        yield browser, page


class ContentTyper(Tool):
    name = "check_url_content_type"
    description = "Tool to check the content type of a remote URL, if it is not clear what the content type may be, e.g. to check if it's an image, audio, etc."
    inputs = {
        "url": {
            "type": "string",
            "description": "URL to check the content type of",
        }
    }
    output_type = "string"

    def forward(self, url: str):
        try:
            response = requests.head(url)
            if response.status_code < 400:
                return response.headers.get("Content-Type")
        except Exception as exc:
            print(f"Failed to determine content type of {url}: {exc}")
        return "Could not determine content type."


class WebsiteFetcher(Tool):
    name = "visit_webpage"
    description = (
        "Tool to fetch the content of URLs (unless they are tweets, this cannot be used for twitter/x). "
        "This tool is particularly useful in extracting information from direct source material to answer questions, "
        "and must always be used subsequent to web search results unless the task is specifically to perform a web search only."
    )
    inputs = {
        "url": {
            "type": "string",
            "description": "Webpage URL to visit",
        },
        "selector": {
            "type": "string",
            "nullable": True,
            "description": "BeautifulSoup selector, to filter specific items/types of items from the resulting HTML content, e.g. 'img' to find images, 'a' to find links, etc.",
        },
    }
    output_type = "string"

    def forward(self, url: str, selector: str = None) -> str:
        with get_browser() as (browser, page):
            return_value = "Website could not be fetched: {url}"
            try:
                try:
                    page.goto(url, wait_until="networkidle", timeout=10000)
                except TimeoutError:
                    ...
                html = page.content()
                if not selector:
                    markdown_content = markdownify(html).strip()
                    return_value = re.sub(r"\n{3,}", "\n\n", markdown_content)
                else:
                    return_value = html
            except Exception as exc:
                # Fallback to requests static/no JS fetch.
                print(f"Error fetching {url} with chrome: {exc}")
                try:
                    response = requests.get(url, timeout=10000)
                    response.raise_for_status()
                    markdown_content = markdownify(response.text).strip()
                    return_value = re.sub(r"\n{3,}", "\n\n", markdown_content)
                except Exception as fallback_exc:
                    print(f"Error fetching {url} with fallback: {fallback_exc}")
            finally:
                browser.close()
            if selector:
                soup = BeautifulSoup(return_value, "html.parser")
                try:
                    elements = soup.select(selector)
                    return_value = "\n".join([str(element) for element in elements])
                except Exception as exc:
                    print(f"Error parsing selector '{selector}': {exc}")
            return return_value


class WebsiteScreenshotter(Tool):
    name = "screenshot_webpage"
    description = (
        "Tool to generate images from URLs, by visiting the URL with a headless browser and taking "
        "a screenshot after dynamic content has loaded. The output is the path on disk to the image/screenshot."
    )
    inputs = {
        "url": {
            "type": "string",
            "description": "URL to generate screenshot of",
        },
        "mobile": {
            "type": "boolean",
            "nullable": True,
            "description": "simulate mobile browser view instead of standard/desktop",
        },
    }
    output_type = "string"

    def forward(self, url: str, mobile: bool = False) -> str:
        user_agent = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
            if mobile
            else "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        viewport = {"width": 390, "height": 844} if mobile else {"width": 1920, "height": 1080}
        with get_browser(user_agent=user_agent, viewport=viewport) as (browser, page):
            try:
                try:
                    page.goto(url, wait_until="networkidle", timeout=10000)
                except TimeoutError:
                    ...
                screenshot = page.screenshot()
                with tempfile.NamedTemporaryFile(
                    mode="wb", delete=False, suffix=".webp"
                ) as tmpfile:
                    tmpfile.close()
                    Image.open(io.BytesIO(screenshot)).save(tmpfile.name)
                    return tmpfile.name
            except Exception as exc:
                print(f"Screenshot could not be generated: {exc}")
            finally:
                browser.close()
            return None


class Downloader(Tool):
    name = "download"
    description = "Tool to download the contents of a remote URL, when the content type of the remote URL is something other than text, e.g. images, videos, audio files, etc."
    inputs = {
        "url": {
            "type": "string",
            "description": "URL of the content to download",
        }
    }
    output_type = "string"

    def forward(self, url: str):
        response = requests.get(url, stream=True)
        if response.status_code < 400:
            with tempfile.NamedTemporaryFile(mode="wb", delete=False) as outfile:
                for chunk in response.iter_content(chunk_size=8192):
                    outfile.write(chunk)
                return outfile.name
        else:
            raise Exception("Failed to download!")


class WebSearcher(Tool):
    name = "web_search"
    description = (
        "Tool for performing web searches to find URLs and summary information related to a topic."
    )
    inputs = {
        "query": {
            "type": "string",
            "description": "Search query string to use when performing the search.",
        },
        "top_n": {
            "type": "integer",
            "nullable": True,
            "description": "Use a reranking model to return only the top top_n results.",
        },
        "filter_domains_csv": {
            "type": "string",
            "nullable": True,
            "description": "If (AND ONLY IF) the task given from the user/input tweet requires using specific domains, the CSV of domains to limit search results to.",
        },
        "kwargs": {
            "type": "object",
            "description": (
                "Optional search flags/settings to augment, limit, or filter results. "
                "Treat this as normal python kwargs, not a dict. "
                "Supported kwargs are the following (but do not include 'query'): "
                f"{BraveSearchParams.model_json_schema()}"
            ),
        },
    }
    output_type = "string"

    def forward(self, query: str, top_n: int = 5, filter_domains_csv: str = None, **kwargs):
        query = re.sub(r"site:[^ ]\s*", "", query)
        if filter_domains_csv:
            query += " " + " ".join([f"site:{domain}" for domain in filter_domains_csv.split(",")])
        params = {"q": query}
        params.update(kwargs)
        result = requests.post(
            f"{settings.squad_api_base_url}/data/brave/search",
            json=params,
            headers={
                "Authorization": settings.authorization,
            },
        )
        result.raise_for_status()
        raw_result = result.json()
        if not raw_result.get("web", {}).get("results"):
            return "No search results found."
        search_results = raw_result["web"]["results"]
        summary_keys = [
            "title",
            "description",
            "extra_snippets",
            "url",
            "age",
            "page_age",
            "subtype",
        ]
        singular_results = []
        for item in search_results:
            summary = []
            summary_data = {
                key: value if isinstance(value, str) else "\n".join(value)
                for key, value in item.items()
                if key in summary_keys
            }
            for key in summary_keys:
                value = summary_data.get(key)
                if not value:
                    continue
                summary.append(f"{key}: {value}")
            if item.get("thumbnail"):
                summary.append(f"image: {item['thumbnail']['original']}")
            if item.get("video") and item["video"].get("thumbnail", {}).get("original"):
                summary.append(
                    f"video: {item['video'].get('duration', 'unknown duration')} thumbnail: {item['video']['thumbnail']['original']}"
                )
            singular_results.append("\n".join(summary))
        if top_n is not None and singular_results:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(
                rerank(query, singular_results, top_n=top_n, auth=settings.authorization)
            )
        return "\n---\n".join(singular_results[: top_n or 5])
