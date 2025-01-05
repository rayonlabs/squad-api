"""
Settings used in creating OpenSearch indices for agent memory, tweet index, etc.
"""

import os
import aiohttp
import backoff
from loguru import logger
from copy import deepcopy
from langdetect import detect


# Default shard/replica counts.
DEFAULT_SHARD_COUNT = int(os.getenv("SHARD_COUNT", "1"))
DEFAULT_REPLICA_COUNT = int(os.getenv("REPLICA_COUNT", "0"))

# List of supported languages (for stemming), which also must be supported here:
# see "stemmer" in https://opensearch.org/docs/latest/analyzers/token-filters/index/
SUPPORTED_LANGUAGES = [
    "arabic",
    "armenian",
    "basque",
    "bengali",
    "brazilian",
    "bulgarian",
    "catalan",
    "czech",
    "danish",
    "dutch",
    "dutch_kp",
    "english",
    "lovins",
    "estonian",
    "finnish",
    "french",
    "galician",
    "german",
    "greek",
    "hindi",
    "hungarian",
    "indonesian",
    "irish",
    "italian",
    "latvian",
    "Lithuanian",
    "norwegian",
    "portuguese",
    "romanian",
    "russian",
    "sorani",
    "spanish",
    "swedish",
    "turkish",
]

# Convert langdetect codes to the opensearch stemmer keyword.
LANGDETECT_TO_OPENSEARCH = {
    "ar": "arabic",
    "hy": "armenian",
    "eu": "basque",
    "bn": "bengali",
    "pt-br": "brazilian",
    "bg": "bulgarian",
    "ca": "catalan",
    "cs": "czech",
    "da": "danish",
    "nl": "dutch",
    "en": "english",
    "et": "estonian",
    "fi": "finnish",
    "fr": "french",
    "gl": "galician",
    "de": "german",
    "el": "greek",
    "hi": "hindi",
    "hu": "hungarian",
    "id": "indonesian",
    "ga": "irish",
    "it": "italian",
    "lv": "latvian",
    "lt": "lithuanian",
    "no": "norwegian",
    "pt": "portuguese",
    "ro": "romanian",
    "ru": "russian",
    "ck": "sorani",
    "es": "spanish",
    "sv": "swedish",
    "tr": "turkish",
}

# Dynamic templates for strings.
DYNAMIC_TEXT_MAPPINGS = [
    {
        "standard_text_field": {
            "match_mapping_type": "string",
            "match": f"*_text_{lang}",
            "mapping": {
                "type": "text",
                "fields": {
                    "keyword": {
                        "type": "keyword",
                        "ignore_above": 256,
                    },
                    "stem": {
                        "type": "text",
                        "analyzer": f"{lang}_analyzer",
                    },
                },
            },
        },
    }
    for lang in SUPPORTED_LANGUAGES
]

# Custom analyzers.
ANALYZERS = {
    f"{lang}_analyzer": {
        "tokenizer": "standard",
        "filter": [
            "lowercase",
            f"{lang}_stemmer",
        ],
    }
    for lang in SUPPORTED_LANGUAGES
}

# Stemmers per language.
FILTERS = {f"{lang}_stemmer": {"type": "stemmer", "language": lang} for lang in SUPPORTED_LANGUAGES}

# Date fields.
DYNAMIC_DATE_MAPPINGS = {
    "standard_date_field": {
        "match_mapping_type": "date",
        "match": "*_date",
        "mapping": {
            "type": "date",
        },
    },
}

# Term fields.
DYNAMIC_TERM_MAPPINGS = {
    "standard_term_field": {
        "match_mapping_type": "string",
        "match": "*_term",
        "mapping": {
            "type": "keyword",
            "normalizer": "lowercase",
        },
    },
}

# Numeric.
DYNAMIC_NUM_MAPPINGS = {
    "standard_num_field": {
        "match_mapping_type": "*",
        "match": "*_num",
        "mapping": {
            "type": "float",
        },
    },
}

# Boolean.
DYNAMIC_BOOL_MAPPINGS = {
    "standard_bool_field": {
        "match_mapping_type": "boolean",
        "match": "*_bool",
        "mapping": {
            "type": "boolean",
        },
    },
}

# Combine the mappings values.
MAPPINGS = {
    "dynamic_templates": (
        DYNAMIC_TEXT_MAPPINGS
        + [
            DYNAMIC_DATE_MAPPINGS,
            DYNAMIC_TERM_MAPPINGS,
            DYNAMIC_NUM_MAPPINGS,
        ]
    ),
    "_source": {
        "enabled": True,
    },
}


def detect_language(text: str) -> str:
    """
    Attempt language detection.
    """
    try:
        return LANGDETECT_TO_OPENSEARCH.get(detect(text), "english")
    except Exception as exc:
        logger.error(f"Error performing language detection: {exc}")
    return "english"


@backoff.on_exception(
    backoff.constant,
    Exception,
    jitter=None,
    interval=3,
    max_tries=5,
)
async def generate_embeddings(text: str, api_key: str) -> list[float]:
    """
    Generate embeddings with bge-m3 (multi-lingual) for a given input text.
    """
    async with aiohttp.ClientSession(raise_for_status=True) as session:
        async with session.post(
            "https://chutes-baai-bge-m3.chutes.ai/embed",
            json={
                "inputs": text,
            },
            headers={
                "Authorization": api_key,
            },
        ) as resp:
            return (await resp.json())[0]


def generate_template(
    index_prefix: str,
    shard_count: int = DEFAULT_SHARD_COUNT,
    replica_count: int = DEFAULT_REPLICA_COUNT,
    embedding_weight: float = 0.5,
    **static_mappings,
) -> dict:
    """
    Generate index templates (and hybrid search pipelines to match).
    """
    mappings = deepcopy(MAPPINGS)
    mappings["properties"] = static_mappings

    # Index template.
    template = {
        "index_patterns": [f"{index_prefix}-*"],
        "template": {
            "settings": {
                "index.search.default_pipeline": f"{index_prefix}-pipeline",
                "index.refresh_interval": "1s",
                "knn": True,
                "knn.algo_param.ef_search": 100,
                "number_of_shards": shard_count,
                "number_of_replicas": replica_count,
                "analysis": {
                    "analyzer": ANALYZERS,
                    "filter": FILTERS,
                },
            },
            "mappings": mappings,
        },
        "_meta": {
            "description": f"Index template for {index_prefix}",
        },
    }

    # Search pipeline to go along with it.
    pipeline = {
        "description": f"Hybrid search postprocessor for {index_prefix}",
        "phase_results_processors": [
            {
                "normalization-processor": {
                    "normalization": {"technique": "min_max"},
                    "combination": {
                        "technique": "arithmetic_mean",
                        "parameters": {"weights": [embedding_weight, 1.0 - embedding_weight]},
                    },
                },
            },
        ],
    }

    return template, pipeline
