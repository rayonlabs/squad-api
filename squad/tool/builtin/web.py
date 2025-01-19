import io
import re
import requests
import tempfile
from PIL import Image
from playwright.sync_api import sync_playwright
from markdownify import markdownify
from smolagents import Tool


class ContentTypeTool(Tool):
    name = "check_url_content_type"
    description = "This is a tool that sends a HEAD request to a remote URL and returns the Content-Type header from the response."
    inputs = {
        "url": {
            "type": "string",
            "description": "URL to check the content type of",
        }
    }
    output_type = "string"

    def forward(self, url: str):
        response = requests.head(url)
        if response.status_code < 400:
            return response.headers.get("Content-Type")
        else:
            raise Exception("Failed to retrieve Content-Type header!")


class WebsiteFetcher(Tool):
    name = "visit_webpage"
    description = (
        "Visits a webpage, waits for dynamic content to load, and returns content as markdown."
    )
    inputs = {"url": {"type": "string", "description": "Webpage URL to visit"}}
    output_type = "string"

    def forward(self, url: str) -> str:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
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
            try:
                page.goto(url, wait_until="networkidle", timeout=10000)
                html = page.content()
                markdown_content = markdownify(html).strip()
                return re.sub(r"\n{3,}", "\n\n", markdown_content)
            except Exception:
                # Fallback to requests static/no JS fetch.
                response = requests.get(url)
                response.raise_for_status()
                markdown_content = markdownify(response.text).strip()
                return re.sub(r"\n{3,}", "\n\n", markdown_content)
            finally:
                browser.close()


class WebsiteScreenshotter(Tool):
    name = "screenshot_webpage"
    description = "Visits a webpage, waits for dynamic content to load, and takes a screenshot."
    inputs = {
        "url": {
            "type": "string",
            "description": "URL to generate screenshot of",
        },
        "mobile": {
            "type": "boolean",
            "description": "simulate mobile browser view instead of standard/desktop",
        },
    }
    output_type = "image"

    def forward(self, url: str, mobile: bool) -> str:
        user_agent = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
            if mobile
            else "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        viewport = {"width": 390, "height": 844} if mobile else {"width": 1920, "height": 1080}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=user_agent,
                viewport=viewport,
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
            try:
                page.goto(url, wait_until="networkidle", timeout=10000)
                screenshot = page.screenshot()
                return Image.open(io.BytesIO(screenshot))
            finally:
                browser.close()


class Downloader(Tool):
    name = "download"
    description = "This is a tool to the content of remote URLs, when the content is not text/*, e.g. images, audio, video, etc, returning the local path of the downloaded content."
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
