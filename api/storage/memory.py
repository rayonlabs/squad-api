"""
Arbitrary (user-defined) memories, which can be conversation data, knowledge bank items, etc.
"""

import uuid
from typing import List
from loguru import logger
from pydantic import BaseModel, Field
from datetime import datetime
from async_lru import alru_cache
from api.config import settings
from api.storage.base import (
    detect_language,
    generate_embeddings,
    generate_template,
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
            "engine": "nmslib",
            "space_type": "cosinesimil",
            "name": "hnsw",
            "parameters": {"ef_construction": 256, "m": 32},
        },
    },
}


class Memory(BaseModel):
    uid: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        title="UID",
        description="UID of the memory.",
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
    )
    text: str = Field(
        title="Text",
        description="The full text of the memory.",
    )
    topics: List[str] = Field(
        [],
        title="Topics",
        description="List of topics discussed.",
    )
    emotions: List[str] = Field(
        [],
        title="Emotions",
        description="Emotions expressed during the discussion.",
    )
    personal_info: List[str] = Field(
        [],
        title="Key concepts",
        description="List of key personality traits, interests, job, education, life goals, hobbies, pet names, or any other type of personal information that is shared.",
    )
    sentiment: str = Field(
        "neutral",
        title="Sentiment",
        description="Sentiment analysis of the memory.",
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
            "meta": self.meta,
            "default_text": self.text,
            "language_term": self.language,
            f"memory_text_{self.language}": self.text,
            "sentiment_term": self.sentiment,
            f"topics_text_{self.language}": self.topics,
            f"emotions_text_{self.language}": self.emotions,
            f"personal_info_text_{self.language}": self.personal_info,
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
            meta=doc["meta"],
            language=language,
            text=doc["default_text"],
            sentiment=doc.get("sentiment_term", "neutral"),
            topics=doc[f"topics_text_{language}"],
            emotions=doc[f"emotions_text_{language}"],
            personal_info=doc[f"personal_info_text_{language}"],
            timestamp=datetime.fromisoformat(doc["memory_date"]),
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
