"""Whether a brand is named in a text, as the sources that look for brands in what they
collect read it: the AI answers source in an answer, and Search Console in a query.

A brand is named when its name or one of its aliases is in the text as whole words, in any
letter case. A name that is only part of a longer word is not. Where it is named, its first
mention is the earliest place in the text where any of its names is found so.
"""
from __future__ import annotations

import re

from brand_presence.client_ref import Brand


def _whole(name: str) -> re.Pattern[str] | None:
    """A name as whole words, any space between them, in any letter case. None for a name
    with no word in it."""
    words = name.split()
    if not words:
        return None
    return re.compile(r"(?<!\w)" + r"\s+".join(re.escape(word) for word in words) + r"(?!\w)",
                      re.IGNORECASE)


def named_by(text: str, brand: Brand) -> str | None:
    """The longest of a brand's name and aliases that is in the text as whole words, in any
    letter case, or None where none is."""
    found = None
    for name in (brand.name, *brand.aliases):
        whole = _whole(name)
        if whole is not None and whole.search(text) and (found is None or len(name) > len(found)):
            found = name
    return found


def first_named_at(text: str, brand: Brand) -> int | None:
    """Where the brand is first named in the text: the number of characters before the
    earliest whole-word match of its name or any alias. None where it is not named, so it
    is None exactly where `is_named` is False."""
    places = []
    for name in (brand.name, *brand.aliases):
        whole = _whole(name)
        match = whole.search(text) if whole is not None else None
        if match is not None:
            places.append(match.start())
    return min(places, default=None)


def is_named(text: str, brand: Brand) -> bool:
    """Whether a brand's name or one of its aliases is in the text as whole words, in any
    letter case. A name that is only part of a longer word is not."""
    return named_by(text, brand) is not None
