"""Split a pictogram name into object, meaning, view and variety words."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from ..composition.names import CONNECTORS, OPERATORS
from ..concepts import SIZES, STYLE, split_name, wordnet

OBJECT_LEXNAMES = {
    "noun.artifact",
    "noun.object",
    "noun.animal",
    "noun.plant",
    "noun.food",
    "noun.body",
    "noun.person",
    "noun.substance",
    "noun.shape",
}
VIEWS = {"front", "side", "top", "bottom", "three-quarter", "isometric", "partial", "full", "unknown"}
VIEW_WORDS = {
    "top": "top",
    "topview": "top",
    "overhead": "top",
    "above": "top",
    "bottom": "bottom",
    "below": "bottom",
    "side": "side",
    "profile": "side",
    "front": "front",
    "back": "front",
    "iso": "isometric",
    "isometric": "isometric",
    "3d": "three-quarter",
    "perspective": "three-quarter",
    "partial": "partial",
    "half": "partial",
}
# name word -> visible feature it announces (None: a style word, not a feature)
VARIETY_WORDS = {
    "hot": "steam",
    "steam": "steam",
    "steaming": "steam",
    "saucer": "saucer",
    "lid": "lid",
    "open": "open",
    "closed": "closed",
    "empty": "empty",
    "full": "full",
    "handle": "handle",
    "straw": "straw",
    "ice": "ice",
}


# Composite operators that structure a glyph rather than carry meaning.
# Modifier words (download, add, lock) stay: they name meanings or objects.
STRUCTURAL_ROLES = {"negation", "frame", "repetition"}


@dataclass
class NameRoles:
    object_tokens: list[str] = field(default_factory=list)
    meaning_tokens: list[str] = field(default_factory=list)
    view: str = "unknown"
    varieties: list[str] = field(default_factory=list)


@lru_cache(maxsize=100_000)
def is_object_word(token: str) -> bool:
    """A thing that can be drawn: one of the two most frequent noun senses is
    an artifact, natural object, animal, plant, food, body part, person,
    substance or shape."""
    return any(s.lexname() in OBJECT_LEXNAMES for s in wordnet().synsets(token, "n")[:2])


@lru_cache(maxsize=100_000)
def _is_word(token: str) -> bool:
    return bool(wordnet().synsets(token))


def name_roles(name: str) -> NameRoles:
    r = NameRoles()
    varieties: set[str] = set()
    for t in split_name(name):
        if t in STYLE or t in SIZES or t in CONNECTORS:
            continue
        if t in VIEW_WORDS:
            r.view = VIEW_WORDS[t]
        elif t in VARIETY_WORDS:
            varieties.add(VARIETY_WORDS[t])
        elif len(t) < 3 or OPERATORS.get(t, ("",))[0] in STRUCTURAL_ROLES or not _is_word(t):
            # short words ("as" is arsenic in WordNet), structural operators
            # (composite analysis) and codes are neither objects nor meanings
            continue
        elif is_object_word(t):
            r.object_tokens.append(t)
        else:
            r.meaning_tokens.append(t)
    r.varieties = sorted(varieties)
    return r
