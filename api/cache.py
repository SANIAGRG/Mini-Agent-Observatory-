import redis
import hashlib
import json
import os

# Deliberately a different Redis "database number" (/1) than Celery's
# broker/backend (/0) - same Redis container, but cache keys and queue
# keys never mix.
CACHE_REDIS_URL = os.environ.get("CACHE_REDIS_URL", "redis://localhost:6379/1")
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "3600"))

_client = redis.from_url(CACHE_REDIS_URL, decode_responses=True)


def make_cache_key(model: str, prompt: str) -> str:
    # Hash of model + prompt together - if either changes, it's a
    # different key, so a cache hit only happens on a true exact repeat.
    raw = f"{model}:{prompt}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"llm_cache:{digest}"


def get_cached(model: str, prompt: str):
    key = make_cache_key(model, prompt)
    value = _client.get(key)
    if value is None:
        return None
    return json.loads(value)


def set_cached(model: str, prompt: str, response_data: dict):
    key = make_cache_key(model, prompt)
    _client.set(key, json.dumps(response_data), ex=CACHE_TTL_SECONDS)