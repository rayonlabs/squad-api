"""
Arbitrary (user-defined) memories, which can be conversation data, knowledge bank items, etc.
"""

import re
import uuid
from loguru import logger
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime, UTC
from async_lru import alru_cache
from squad.config import settings
from squad.storage.base import (
    detect_language,
    generate_embeddings,
    generate_template,
    SUPPORTED_LANGUAGES,
)


STATIC_FIELDS = {
    # Lanuage, if non-english.
    "language": {
        "type": "keyword",
    },
    # Arbitrary meta-data.
    "meta": {
        "type": "object",
        "enabled": False,
    },
    # Default (english) text.
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
    # Multi-lingual embeddings vector (via bge-m3)
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


class Memory(BaseModel):
    uid: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        title="UID",
        description="UID of the memory.",
    )
    agent_id: str = Field(
        title="Agent UID",
        description="UID of the agent this memory belongs to.",
    )
    session_id: Optional[str] = Field(
        None,
        title="Session UID",
        description="UID of the session, leave None for global memories.",
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

    async def indexable(self, api_key: str):
        """
        Return the memory as an indexable document, with embeddings.
        """
        if not self.language:
            self.language = detect_language(self.text)
        return {
            "uid_term": self.uid,
            "agent_id_term": self.agent_id,
            "session_id_term": self.session_id,
            "meta": self.meta,
            "default_text": self.text,
            "language_term": self.language,
            f"memory_text_{self.language}": self.text,
            "memory_date": self.timestamp.replace(tzinfo=None).isoformat().rstrip("Z"),
            "created_from_text_{self.language}": self.created_from,
            "embeddings": await generate_embeddings(self.text, api_key),
        }

    @staticmethod
    def from_index(doc):
        """
        Convert index document source data into Memory instance.
        """
        language = doc.get("language_term", "english")
        return Memory(
            uid=doc["uid_term"],
            agent_id=doc["agent_id_term"],
            session_id=doc.get("session_id_term"),
            meta=doc["meta"],
            language=language,
            text=doc["default_text"],
            timestamp=datetime.fromisoformat(doc["memory_date"]),
        )


async def index_memories(memories: list[Memory], api_key: str) -> None:
    """
    Index memories.
    """
    if not memories:
        return
    logger.info(f"Attempting to index {len(memories)} memories...")
    bulk_body = []
    for memory in memories:
        bulk_body += [
            {
                "create": {
                    "_index": f"memories-{settings.memory_index_version}",
                    "_id": str(memory.uid),
                }
            },
            await memory.indexable(api_key),
        ]
    result = await settings.opensearch_client.bulk(  # noqa
        body=bulk_body,
        refresh=True,
    )
    # XXX Could look through each individual doc result and handle retries and stuff eventually...
    logger.success(f"Successfully indexed {len(memories)} memories.")


async def search(
    agent_id: str,
    session_id: Optional[str] = None,
    text: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    only_semantic: bool = False,
    only_keyword: bool = False,
    date_decay: bool = True,
    sort: Optional[list[dict[str, str]]] = None,
    limit: Optional[int] = 10,
    api_key: str = None,
    language: str = None,
    **kwargs,
) -> tuple[list[dict], dict]:
    query = {}

    # Detect the language first, so we can use the best field regardless of other params.
    if not language:
        language = "english" if not text else (detect_language(text) or "english")

    # Hard filters (usernames and date ranges).
    filters = [{"term": {"agent_id_term": agent_id}}]
    if start_date:
        filters.append({"range": {"memory_date": {"gte": start_date.isoformat()}}})
    if end_date:
        filters.append({"range": {"memory_date": {"lte": end_date.isoformat()}}})
    if session_id:
        filters.append({"term": {"session_id_term": session_id}})
    bool_filter = {"bool": {"must": filters}}

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
                        f"memory_text_{language}",
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

    # Optionally add a date decay function to boost more recent memories.
    if date_decay:
        epoch_date = datetime(year=2025, month=1, day=1)
        now = datetime.now(UTC).replace(tzinfo=None)
        keyword_query = {
            "function_score": {
                "functions": [
                    {
                        "gauss": {
                            "memory_date": {
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
    import json

    print(json.dumps(body, indent=2))
    response = await settings.opensearch_client.search(
        index=f"memories-{settings.memory_index_version}",
        body=body,
    )
    memories = [Memory.from_index(doc["_source"]) for doc in response["hits"]["hits"]]
    return memories, response


async def delete(agent_id: str, memory_id: str) -> dict:
    """
    Delete a memory.
    """
    assert isinstance(memory_id, str) and re.match(r"^[a-f0-9-]+$", memory_id)
    return await settings.opensearch_client.delete_by_query(
        index=f"memories-{settings.memory_index_version}",
        body={
            "query": {
                "bool": {
                    "must": [
                        {
                            "term": {
                                "agent_id": agent_id,
                            },
                        },
                        {
                            "term": {
                                "uid_term": memory_id,
                            },
                        },
                    ],
                },
            },
        },
    )


@alru_cache(maxsize=1)
async def initialize():
    """
    Ensure the index is initialized in OpenSearch.
    """
    template_name = "memories-template"
    pipeline_name = "memories-pipeline"
    index_name = f"memories-{settings.memory_index_version}"
    template, pipeline = generate_template(
        "memories",
        shard_count=settings.memory_index_shards,
        replica_count=settings.memory_index_replicas,
        embedding_weight=settings.memory_embed_weight,
        **STATIC_FIELDS,
    )
    if await settings.opensearch_client.indices.exists(index=index_name):
        logger.info(f"Index already exists: {index_name}")
        return True
    if not await settings.opensearch_client.indices.exists_index_template(template_name):
        logger.info(f"Creating index template: {template_name}")
        await settings.opensearch_client.indices.put_index_template(
            name=template_name,
            body=template,
        )
        await settings.opensearch_client.http.put(
            f"/_search/pipeline/{pipeline_name}", body=pipeline
        )
    else:
        logger.info(f"Index template already exists: {template_name}")
    logger.info(f"Performing empty PUT to /{index_name} to bootstrap index...")
    await settings.opensearch_client.http.put(f"/{index_name}")
    return True
