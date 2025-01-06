"""
Storage/retrieval/settings/etc. for X (tweets? WTF are they called now?)
"""

import time
import uuid
import opensearchpy
from loguru import logger
from datetime import datetime, UTC
from typing import Optional
from pydantic import BaseModel
from async_lru import alru_cache
from squad.config import settings
from squad.storage.base import (
    detect_language,
    generate_embeddings,
    generate_template,
)


class Tweet(BaseModel):
    id: int
    user_id: int
    username: str
    timestamp: datetime
    quote_count: Optional[int] = 0
    retweet_count: Optional[int] = 0
    reply_count: Optional[int] = 0
    favorite_count: Optional[int] = 0
    text: str
    language: Optional[str] = "english"
    attachments: Optional[list[dict]] = []

    @staticmethod
    def from_index(doc):
        return Tweet(
            id=int(doc["id_num"]),
            username=doc["username_term"],
            user_id=int(doc["user_id_term"]),
            timestamp=datetime.fromisoformat(doc["created_date"]),
            quote_count=int(doc.get("quote_count_num", 0)),
            reply_count=int(doc.get("reply_count_num", 0)),
            retweet_count=int(doc.get("retweet_count_num", 0)),
            favorite_count=int(doc.get("favorite_count_num", 0)),
            text=doc["default_text"],
            language=doc.get("language", "english"),
            attachments=doc.get("attachments", {}),
        )


STATIC_FIELDS = {
    # Always index in english (in addition to language if detected), just in case...
    "default_text": {
        "type": "text",
        "fields": {
            "keyword": {
                "type": "keyword",
                "ignore_above": 256,
            },
            "stem": {
                "type": "text",
                "analyzer": "english_analyzer",
            },
        },
    },
    # Attachments (e.g. images, links, videos).
    "attachments": {
        "type": "object",
        "enabled": False,
    },
    # Lanuage, if non-english.
    "language": {
        "type": "keyword",
    },
    # Always have standard english embeddings (in addition to language if detected), just in case...
    "embeddings": {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {
            "name": "hnsw",
            "engine": "faiss",
            "space_type": "l2",
            "parameters": {
                "m": 64,
                "ef_construction": 512,
            },
        },
    },
}


async def username_to_user_id(username: str) -> int:
    """
    Twitter needs to search based on user IDs (integer), so we need to get that mapping.
    """
    cached = await settings.redis_client.get(f"x:username_to_id:{username}")
    if cached:
        return int(cached)

    result = await settings.tweepy_client.get_user(username=username)
    if not result.data:
        return None
    await settings.redis_client.set(f"kaito:username_to_id:{username}", str(result.data.id))
    return int(result.data.id)


def inject_usernames(results: list[dict]) -> list[dict]:
    """
    Inject usernames into the tweet dictionaries, from the user IDs.
    """
    if not results or not results.data:
        return []
    media_map = {}
    for media in results.includes.get("media", []):
        media_map[media.media_key] = media.data
    tweets = results.data
    tweet_dicts = [tweet.data for tweet in tweets]
    for tweet in tweet_dicts:
        if "attachments" in tweet and "media_keys" in tweet["attachments"]:
            tweet["attachments"] = [
                media_map.get(key) for key in tweet["attachments"]["media_keys"]
            ]
    user_map = {}
    for user in results.includes.get("users", []):
        user_map[str(user.id)] = user.username
    for tweet in tweet_dicts:
        tweet["username"] = user_map.get(str(tweet["author_id"]), str(tweet["author_id"]))
    return tweet_dicts


async def tweet_to_index_format(tweet: dict, api_key: str) -> dict:
    """
    Convert a tweepy tweet dict into the schema used by opensearch with dynamic field names.
    """
    logger.debug(f"Converting tweet {tweet['id']} to indexable format...")
    metrics = tweet.get("public_metrics", {})
    created_at = tweet.get("created_at", datetime.utcnow())
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at.rstrip("Z"))
    created_at = created_at.replace(tzinfo=None)
    language = (
        "english"
        if not tweet["text"] or not tweet["text"].strip()
        else detect_language(tweet["text"])
    )
    doc = {
        "id_num": tweet["id"],
        "user_id_term": tweet["author_id"],
        "username_term": tweet["username"],
        "created_date": created_at.isoformat().rstrip("Z"),
        "quote_count_num": metrics.get("quote_count", 0),
        "reply_count_num": metrics.get("reply_count", 0),
        "retweet_count_num": metrics.get("retweet_count", 0),
        "favorite_count_num": metrics.get("like_count", 0),
        "default_text": tweet["text"],
        "language": language,
        "attachments": tweet.get("attachments"),
    }
    if language != "english":
        doc[f"tweet_text_{language}"] = tweet["text"]

    # Boolean flags for attachment types.
    for attachment in doc.get("attachments") or []:
        doc[f"has_{attachment['type']}_bool"] = True
    if doc.get("attachments"):
        doc["has_attachment_bool"] = True

    # Calculate embeddings.
    if (tweet["text"] or "").strip():
        logger.debug(f"Calculated embeddings for {tweet['text']}")
        doc["embeddings"] = await generate_embeddings(tweet["text"], api_key)

    return doc


async def index_tweets(tweets: list[dict]) -> None:
    """
    Index tweets/replies/etc. via the OpenSearch bulk endpoint.
    """
    if not tweets:
        return
    logger.info(f"Attempting to index {len(tweets)} tweets...")
    bulk_body = []
    for doc in tweets:
        bulk_body.append(
            {
                "update": {
                    "_index": f"tweets-{settings.tweet_index_version}",
                    "_id": str(doc["id_num"]),
                }
            }
        )
        bulk_body.append(
            {
                "doc": doc,
                "doc_as_upsert": True,
            }
        )
    result = await settings.opensearch_client.bulk(  # noqa
        body=bulk_body,
        refresh=True,
    )
    # XXX Could look through each individual doc result and handle retries and stuff eventually...
    logger.success(f"Successfully indexed {len(tweets)} tweets.")


async def most_recent_user_tweet(user_id: int) -> list[dict]:
    """
    Load the most recent indexed tweet for a given username.
    """
    query = {
        "query": {
            "term": {
                "user_id_term": user_id,
            }
        },
        "size": 1,
        "_source": False,
        "sort": [
            {"id_num": {"order": "desc"}},
        ],
    }
    try:
        response = await settings.opensearch_client.search(
            index=f"tweets-{settings.tweet_index_version}",
            body=query,
        )
    except opensearchpy.exceptions.RequestError as exc:
        if "No mapping found for [id_num] in order to sort on" in str(exc):
            return None
        raise
    documents = response["hits"]["hits"]
    if not documents:
        return None
    return documents[0]["_id"]


async def find_and_index_user_tweets(username: str, api_key: str) -> bool:
    """
    Load recent tweets for a given username.
    """
    if (user_id := await username_to_user_id(username)) is None:
        logger.warning(f"Could not find a user_id associated with {username=}")
        return False

    # Make sure we aren't spamming.
    last_attempt = await settings.redis_client.get(f"x:last_user_update:{user_id}")
    if last_attempt and (delta := time.time() - float(last_attempt)) <= 300:
        logger.warning(
            f"Username {username} was last checked {int(delta)} seconds ago, skipping..."
        )
        return False

    # Get the most recent tweet date, to avoid duplicate search work.
    try:
        most_recent_id = await most_recent_user_tweet(user_id)
    except opensearchpy.exceptions.NotFoundError:
        most_recent_id = 0

    # Perform the search.
    results = inject_usernames(
        await settings.tweepy_client.get_users_tweets(
            user_id,
            since_id=most_recent_id,
            tweet_fields=["id", "text", "created_at", "public_metrics", "author_id", "attachments"],
            expansions=["author_id", "attachments.media_keys"],
            media_fields=[
                "alt_text",
                "duration_ms",
                "height",
                "media_key",
                "preview_image_url",
                "public_metrics",
                "type",
                "url",
                "variants",
                "width",
            ],
            max_results=100,
        )
    )

    # Convert to indexable format.
    tweets = [await tweet_to_index_format(item, api_key) for item in results]

    # Index.
    if tweets:
        await index_tweets(tweets)
        await settings.redis_client.set(f"x:last_user_update:{user_id}", str(time.time()))
        return True

    logger.warning(f"No new tweets/replies/reposts found: {username=} since {most_recent_id=}")
    return False


async def get_and_index_tweets(ids: list[int], api_key: str) -> bool:
    """
    Get tweets by their IDs.
    """
    query = {
        "query": {
            "terms": {
                "id_num": ids,
            }
        },
        "size": 1,
        "_source": False,
    }
    response = await settings.opensearch_client.search(
        index=f"tweets-{settings.tweet_index_version}",
        body=query,
    )
    existing_ids = [int(doc["_id"]) for doc in response["hits"]["hits"]]
    to_fetch = list(set([_id for _id in ids if int(_id) not in existing_ids]))
    if not to_fetch:
        logger.warning(f"No new tweets to fetch: {ids}")
        return False

    results = inject_usernames(
        await settings.tweepy_client.get_tweets(
            to_fetch,
            tweet_fields=["id", "text", "created_at", "public_metrics", "author_id", "attachments"],
            expansions=["author_id", "attachments.media_keys"],
            media_fields=[
                "alt_text",
                "duration_ms",
                "height",
                "media_key",
                "preview_image_url",
                "public_metrics",
                "type",
                "url",
                "variants",
                "width",
            ],
        )
    )
    tweets = [await tweet_to_index_format(item, api_key) for item in results]
    if tweets:
        await index_tweets(tweets)
        return True
    logger.warning(f"No new tweets/replies/reposts found: {ids=}")
    return False


async def find_and_index_tweets(search: str, api_key: str) -> bool:
    """
    Load recent tweets by search string instead of username.
    """
    search_id = str(uuid.uuid5(uuid.NAMESPACE_OID, search))
    last_attempt = await settings.redis_client.get(f"x:last_search_time:{search_id}")
    if last_attempt and (delta := time.time() - float(last_attempt)) <= 60:
        logger.warning(
            f"Most recent search for '{search}' was {int(delta)} seconds ago, skipping..."
        )
        return False
    results = await settings.tweepy_client.search_recent_tweets(
        search,
        tweet_fields=["id", "text", "created_at", "public_metrics", "author_id", "attachments"],
        expansions=["author_id", "attachments.media_keys"],
        media_fields=[
            "alt_text",
            "duration_ms",
            "height",
            "media_key",
            "preview_image_url",
            "public_metrics",
            "type",
            "url",
            "variants",
            "width",
        ],
        max_results=100,
    )
    results = [await tweet_to_index_format(t, api_key) for t in inject_usernames(results)]
    await index_tweets(results)


async def search(
    text: Optional[str] = None,
    usernames: Optional[list[str]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    only_semantic: bool = False,
    only_keyword: bool = False,
    date_decay: bool = True,
    sort: Optional[list[dict[str, str]]] = None,
    limit: Optional[int] = 10,
    has: Optional[list[str]] = [],
    api_key: str = None,
) -> tuple[list[Tweet], dict]:
    query = {}

    # Detect the language first, so we can use the best field regardless of other params.
    language = "english" if not text else (detect_language(text) or "english")

    # Hard filters (usernames and date ranges).
    filters = []
    if usernames:
        filters.append({"terms": {"username_term": usernames}})
    if start_date:
        filters.append({"range": {"created_date": {"gte": start_date.isoformat()}}})
    if end_date:
        filters.append({"range": {"created_date": {"lte": end_date.isoformat()}}})
    for attachment_type in has:
        filters.append({"term": {f"has_{attachment_type}_bool": True}})
    bool_filter = None if not filters else {"bool": {"must": filters}}

    # If semantic search is enabled, calculate embeddings and generate KNN search params.
    semantic_query = None
    if text and not only_keyword:
        semantic_query = {
            "knn": {
                "embeddings": {
                    "vector": await generate_embeddings(text, api_key),
                    "k": limit * 5,
                }
            }
        }
        if bool_filter:
            semantic_query["knn"]["embeddings"]["filter"] = bool_filter

    # If keyword search is enabled, build the query with multi-lingual support.
    keyword_query = None
    if text and not only_semantic:
        if language != "english":
            keyword_query = {
                "multi_match": {
                    "query": text,
                    "fields": [
                        "default_text",
                        f"tweet_text_{language}",
                    ],
                }
            }
        else:
            keyword_query = {
                "match": {
                    "default_text": text,
                },
            }
        if bool_filter:
            keyword_query = {
                "bool": {
                    "must": bool_filter["bool"]["must"] + [keyword_query],
                },
            }

    # Optionally add a date decay function to boost more recent tweets.
    if date_decay:
        epoch_date = datetime(year=2025, month=1, day=1)
        now = datetime.now(UTC).replace(tzinfo=None)
        keyword_query = {
            "function_score": {
                "functions": [
                    {
                        "gauss": {
                            "created_date": {
                                "origin": now.isoformat(),
                                "scale": str(int((now - epoch_date).total_seconds())) + "s",
                                "decay": 0.7,
                            },
                        },
                    },
                ],
                "query": keyword_query,
            },
        }

    # Put the whole thing together...
    if not keyword_query and not semantic_query:
        if bool_filter:
            query = bool_filter
        else:
            query["match_all"] = ({},)
    if keyword_query and semantic_query:
        query = {
            "hybrid": {
                "queries": [
                    semantic_query,
                    keyword_query,
                ],
            },
        }
    elif keyword_query:
        query = keyword_query
    else:
        query = semantic_query
    body = {
        "query": query,
        "size": limit,
        "_source": {"excludes": ["*_knn_*", "embeddings"]},
        "track_total_hits": True,
    }
    if sort:
        body["sort"] = sort

    response = await settings.opensearch_client.search(
        index=f"tweets-{settings.tweet_index_version}",
        body=body,
    )
    tweets = [Tweet.from_index(doc["_source"]) for doc in response["hits"]["hits"]]
    return tweets, response


@alru_cache(maxsize=1)
async def initialize():
    """
    Ensure the index is initialized in OpenSearch.
    """
    template_name = "tweets-template"
    pipeline_name = "tweets-pipeline"
    index_name = f"tweets-{settings.tweet_index_version}"
    template, pipeline = generate_template(
        "tweets",
        shard_count=settings.tweet_index_shards,
        replica_count=settings.tweet_index_replicas,
        embedding_weight=settings.tweet_embed_weight,
        **STATIC_FIELDS,
    )
    if await settings.opensearch_client.indices.exists(index=index_name):
        return True
    if not await settings.opensearch_client.indices.exists_index_template(template_name):
        await settings.opensearch_client.indices.put_index_template(
            name=template_name,
            body=template,
        )
        await settings.opensearch_client.http.put(
            f"/_search/pipeline/{pipeline_name}", body=pipeline
        )
    else:
        logger.info(f"Index template already exists: {template_name}")
    await settings.opensearch_client.http.put(f"/{index_name}")
    return True
