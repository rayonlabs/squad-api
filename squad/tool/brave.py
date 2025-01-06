"""
Brave internet search tools.
"""

from munch import munchify
from squad.config import settings

SEARCH_PATH = "/res/v1/web/search"


async def search(
    query: str,
    country: str = None,
    search_lang: str = None,
    ui_lang: str = None,
    count: int = 20,
    offset: int = 0,
    safesearch: str = "moderate",
    freshness: str = None,
    text_decorations: bool = False,
    spellcheck: bool = False,
    result_filter: str = None,
    # XXX support googles maybe? not for now
    units: str = "imperial",  # Metric really is the only sane option here, but...
    extra_snippets: bool = False,
    summary: bool = False,
):
    params = {}
    for key, value in locals().items():
        if value and key not in ("query", "params", "value") and not key.startswith("_"):
            params[key] = str(value)
    params["q"] = query
    async with settings.brave_sm.get_session() as session:
        async with session.get(SEARCH_PATH, params=params) as resp:
            return munchify(await resp.json())
