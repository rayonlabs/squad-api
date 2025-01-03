"""
Settings used in creating OpenSearch indices for X (tweets? WTF are they called now?)
"""

import time
import uuid
from loguru import logger
from datetime import datetime
from api.config import settings
from api.storage.base import (
    detect_language,
    generate_embeddings,
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
    # Lanuage, if non-english.
    "language": {
        "type": "keyword",
    },
    # Always have standard english embeddings (in addition to language if detected), just in case...
    "embeddings": {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {
            "engine": "nmslib",
            "space_type": "cosinesimil",
            "name": "hnsw",
            "parameters": {"ef_construction": 256, "m": 32},
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
    tweets = results.data
    tweet_dicts = [tweet.data for tweet in tweets]
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
    metrics = tweet.get("public_metrics", {})
    created_at = tweet.get("created_at", datetime.utcnow())
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at.rstrip("Z"))
    created_at = created_at.replace(tzinfo=None)
    language = detect_language(tweet["text"])
    doc = {
        "id_num": tweet["id"],
        "user_id_term": tweet["user_id"],
        "username_term": tweet["username"],
        "created_date": created_at,
        "quote_count_num": metrics.get("quote_count", 0),
        "reply_count_num": metrics.get("reply_count", 0),
        "retweet_count_num": metrics.get("retweet_count", 0),
        "favorite_count_num": metrics.get("like_count", 0),
        "default_text": tweet["text"],
        "language": language,
    }
    if language != "english":
        doc[f"tweet_text_{language}"] = tweet["text"]

    # Calculate embeddings.
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
    result = await self.search_client.bulk(  # noqa
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
    response = await settings.opensearch_client.search(
        index=f"tweets-{settings.tweet_index_version}",
        body=query,
    )
    documents = response["hits"]["hits"]
    if not documents:
        return None
    return documents[0]["id_num"]


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
    most_recent_id = await most_recent_user_tweet(user_id)

    # Base X api args.
    call_args = dict(
        since_id=most_recent_id,
        tweet_fields=["id", "text", "created_at", "public_metrics", "author_id"],
        expansions=["author_id"],
        max_results=500,
        exclude=["retweets", "replies"],
    )

    # First we call excluding retweets and replies.
    primary_results = await settings.tweepy_client.get_users_tweets(user_id, **call_args)

    # Then we call excluding nothing, which will hopefully scoop up all replies and retweets.
    call_args["exclude"] = None
    secondary_results = await settings.tweepy_client.get_users_tweets(user_id, **call_args)

    # Get the unique subset.
    primary = inject_usernames(primary_results)
    secondary = inject_usernames(secondary_results)
    unique = {}
    for batch in [primary, secondary]:
        if not batch:
            continue
        for item in batch:
            unique[item["id"]] = item

    # Convert to indexable format.
    tweets = [await tweet_to_index_format(item) for item in unique.values()]

    # Index.
    if tweets:
        await index_tweets(tweets)
        await settings.redis_client.set(f"x:last_user_update:{user_id}", str(time.time()))
        return True

    logger.warning(f"No new tweets/replies/reposts found: {username=}")
    return False


async def find_and_index_tweets(search: str) -> bool:
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
    results = await settings.opensearch_client.search_recent_tweets(
        search,
        tweet_fields=["id", "text", "created_at", "public_metrics", "author_id"],
        expansions=["author_id"],
        max_results=500,
    )
    results = inject_usernames(results)
    await index_tweets(results)
