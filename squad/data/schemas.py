"""
Pydantic models for storage requests (from agents).
"""

from pydantic import BaseModel, Field, field_validator, model_validator, constr
from typing import Optional, List, Dict, Literal, Any
from datetime import datetime
from squad.storage.base import SUPPORTED_LANGUAGES


class BraveSearchParams(BaseModel):
    q: str = Field(
        min_length=1,
        max_length=400,
        description="The search query string",
    )
    country: Optional[str] = Field(
        default=None,
        description=(
            "The search query country, where the results come from. "
            "The country string is limited to 2 character country codes of supported countries."
        ),
    )
    search_lang: Optional[str] = Field(
        default=None,
        description=(
            "The search language preference. "
            "The 2 or more character language code for which the search results are provided."
        ),
    )
    ui_lang: Optional[str] = Field(
        default=None,
        description=(
            "User interface language preferred in response. "
            "Usually of the format ‘<language_code>-<country_code>’."
        ),
    )
    count: int = Field(
        default=10,
        ge=1,
        le=20,
        description=(
            "The number of search results returned in response. "
            "The actual number delivered may be less than requested. "
            "Combine this parameter with offset to paginate search results."
        ),
    )
    offset: int = Field(
        default=0,
        ge=0,
        description=(
            "The zero based offset that indicates number of search results per page (count) to skip before returning the result. "
            "The maximum is 9. The actual number delivered may be less than requested based on the query."
        ),
    )
    safesearch: str = Field(
        default="moderate",
        pattern="^(strict|moderate|off)$",
        description=(
            "Filters search results for adult content. "
            "The following values are supported: "
            "off: No filtering is done, "
            "moderate: Filters explicit content, like images and videos, but allows adult domains in the search results, "
            "strict: Drops all adult content from search results"
        ),
    )
    freshness: Optional[str] = Field(
        default=None,
        description=(
            "Filters search results by when they were discovered. "
            "The following values are supported: "
            "pd: Discovered within the last 24 hours, "
            "pw: Discovered within the last 7 Days, "
            "pm: Discovered within the last 31 Days, "
            "py: Discovered within the last 365 Days, "
            "YYYY-MM-DDtoYYYY-MM-DD: timeframe is also supported by specifying the date range e.g. 2022-04-01to2022-07-30."
        ),
    )
    text_decorations: bool = Field(
        default=False,
        description="Whether display strings (e.g. result snippets) should include decoration markers (e.g. highlighting characters).",
    )
    spellcheck: bool = Field(
        default=False,
        description=(
            "Whether to spellcheck provided query. "
            "If the spellchecker is enabled, the modified query is always used for search. "
            "The modified query can be found in altered key from the query response model."
        ),
    )
    result_filter: Optional[str] = Field(
        default=None,
        description=(
            "A comma delimited string of result types to include in the search response, "
            "e.g. 'web,discussions,locations'"
        ),
    )
    units: str = Field(
        default="imperial",
        pattern="^(imperial|metric)$",
        description=(
            "Unit system for displaying measurements in results. "
            "Possible values are: "
            "metric: The standardized measurement system, "
            "imperial: The British Imperial system of units."
        ),
    )
    extra_snippets: bool = Field(
        default=False, description="Include up to 5 additional excerpts from the search results."
    )
    summary: bool = Field(default=False, description="Whether to include result summaries")

    class Config:
        extra = "forbid"


class BaseSearchArgs(BaseModel):
    text: Optional[str] = Field(default=None, description="Text to search for")
    start_date: Optional[datetime] = Field(
        default=None,
        description="Optional start date filter",
    )
    end_date: Optional[datetime] = Field(
        default=None,
        description="Optional end date filter",
    )
    only_semantic: bool = Field(default=False, description="Whether to use only semantic search")
    only_keyword: bool = Field(default=False, description="Whether to use only keyword search")
    date_decay: bool = Field(
        default=True, description="Whether to apply date decay to search results"
    )
    sort: Optional[List[Dict[str, str]]] = Field(
        default=None, description="List of sort criteria with field and direction"
    )
    limit: Optional[int] = Field(
        default=7, ge=1, le=100, description="Maximum number of search results to return"
    )

    @model_validator(mode="after")
    def validate_search_modes(cls, values):
        if values.only_semantic and values.only_keyword:
            raise ValueError("Cannot set both only_semantic and only_keyword to True")
        return values

    @field_validator("start_date", "end_date")
    def validate_dates(cls, v, info):  # Changed parameters here
        field_name = info.field_name
        data = info.data
        if field_name == "end_date" and v and data.get("start_date"):
            if v < data["start_date"]:
                raise ValueError("end_date must be after start_date")
        return v

    @field_validator("sort")
    def validate_sort_format(cls, v):
        if v is not None:
            valid_directions = {"asc", "desc"}
            for sort_item in v:
                if not isinstance(sort_item, dict) or len(sort_item) != 1:
                    raise ValueError(
                        "Each sort item must be a dictionary with exactly one key-value pair"
                    )
                for direction in sort_item.values():
                    if direction.lower() not in valid_directions:
                        raise ValueError(f"Sort direction must be one of {valid_directions}")
        return v

    class Config:
        extra = "forbid"


class XSearchParams(BaseSearchArgs):
    usernames: Optional[List[constr(pattern=r"^[A-Za-z0-9_\.\-]{1,20}$")]] = Field(
        default=None,
        max_length=20,
        description="List of usernames to filter tweets by (maximum 20 usernames).",
    )
    has: Optional[List[str]] = Field(
        default=[],
        max_length=5,
        description="List of features that tweets must contain, allowed values are 'photo', 'video', 'animated_gif'",
    )


class MemoryArgs(BaseModel):
    session_id: Optional[str] = Field(
        None,
        pattern=r"^[a-z0-9\-]{1,64}$",
        title="Session UID",
        description="UID of the session, or None for global memories.",
    )
    meta: dict[str, str] = Field(
        {},
        title="Metadata",
        description="Arbitrary key/value metadata (not searchable).",
    )
    language: str = Field(
        None,
        title="Language",
        description="Language, auto-detected if not specified.",
        enum=SUPPORTED_LANGUAGES,
    )
    text: str = Field(
        title="Text",
        description="The full text of the memory.",
        min_length=5,
        max_length=20000,
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        title="Timestamp",
        description="Timestamp of this memory.",
    )
    created_from: str = Field(
        None,
        title="Source material summary",
        description="Brief summary of the source the memory was generated from.",
    )


class MemorySearchParams(BaseSearchArgs):
    session_id: Optional[str] = Field(
        None,
        pattern=r"^[a-z0-9\-]{1,64}$",
        title="Session UID",
        description="UID of the session, or None for global memories.",
    )
    language: str = Field(
        None,
        title="Language",
        description="Language, auto-detected if not specified.",
        enum=SUPPORTED_LANGUAGES,
    )
    created_from: str = Field(
        None,
        title="Source material summary",
        description="Brief summary of the source the memory was generated from.",
    )


class DataUniverseSearchParams(BaseModel):
    source: Literal["x", "reddit"] = Field(
        description="Data source identifier, either 'x' or 'reddit'"
    )
    usernames: List[constr(min_length=3, max_length=64)] = Field(
        default=[], min_items=0, max_items=5, description="List of usernames to search, 0-5 allowed"
    )
    keywords: List[constr(min_length=1, max_length=64)] = Field(
        default=[], min_items=0, max_items=5, description="List of keywords to search, 0-5 allowed"
    )
    limit: int = Field(
        default=32, ge=1, le=32, description="Number of results to return, between 1-32"
    )
    start_date: Optional[datetime] = Field(
        default=None, description="Start date for the search in ISO format"
    )
    end_date: Optional[datetime] = Field(
        default=None, description="End date for the search in ISO format", gt_field="start_date"
    )


class ApexWebSearchParams(BaseModel):
    search_query: str = Field(description="Search query")
    limit: int = Field(default=10, ge=1, le=50, description="Maximum number of results to return.")
    miners: int = Field(default=5, ge=1, le=10, description="Number of unique miners to query.")
    timeout: int = Field(default=10, ge=3, le=30, description="Maximum request timeout in seconds.")


class BYOKBody(BaseModel):
    type: str = constr(pattern=r"^(bytes|json)$")
    value: Any


class BYOKParams(BaseModel):
    tool_name: str = Field(description="Name of the tool triggering the request.")
    url: str = Field(
        Description="URL to send the request tool, must match identically to the tool args."
    )
    method: Optional[str] = constr(pattern=r"^(post|put|get|head|patch|delete)$")
    headers: Optional[dict[str, str]] = Field(
        {}, description="Additional request headers to include in request."
    )
    body: Optional[BYOKBody] = Field(description="Request body to send upstream.")
