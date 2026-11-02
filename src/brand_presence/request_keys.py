"""Stable request and attempt keys."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

_HASH_LENGTH = 20



def content_hash(content: object) -> str:
    """A hash of a request's content. The order of an object's names does not change it."""
    text = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_HASH_LENGTH]



def _checked(part: str, what: str) -> str:
    if not part or "/" in part:
        raise ValueError(f"the {what} of a request key is one step with no slash")
    return part



def request_key(client_id: str, source: str, period: str, body: Mapping) -> str:
    return "/".join((
        _checked(client_id, "client ID"), _checked(source, "source"),
        _checked(period, "period"), content_hash(body),
    ))



def sent_key(key: str, attempt: int) -> str:
    """The key the vendor is given. A second attempt adds its number."""
    if attempt < 1:
        raise ValueError("attempts are numbered from 1")
    return key if attempt == 1 else f"{key}/a{attempt}"



def hash_of(key: str) -> str:
    """The hash at the end of a request's key."""
    return key.rsplit("/", 1)[-1]



def object_name(key: str, attempt: int = 1) -> str:
    """The name of the raw object of one attempt at a request, under the run's own path."""
    return f"{hash_of(key)}-a{attempt}.json"

