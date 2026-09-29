"""Name evidence: which operators a pictogram's name (and aliases) announce."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..concepts import SIZES, STYLE, split_name

# token -> (role, label)
OPERATORS: dict[str, tuple[str, str]] = {
    **{t: ("negation", "slash") for t in ("off", "slash", "slashed", "disabled", "no", "not", "crossed", "forbidden", "ban", "prohibited")},
    **{t: ("frame", t) for t in ("circle", "square", "triangle", "octagon", "diamond", "shield", "hexagon")},
    "box": ("frame", "square"),
    "rect": ("frame", "square"),
    "rectangle": ("frame", "square"),
    "rounded": ("frame", "rounded-square"),
    "bubble": ("frame", "bubble"),
    "badge": ("frame", "badge"),
    **{t: ("modifier", "plus") for t in ("plus", "add", "new")},
    **{t: ("modifier", "minus") for t in ("minus", "remove", "subtract")},
    **{t: ("modifier", "check") for t in ("check", "checked", "checkmark", "done", "ok", "verified")},
    **{t: ("modifier", "x") for t in ("x", "close", "cancel", "xmark", "times")},
    **{
        t: ("modifier", t)
        for t in ("lock", "unlock", "clock", "star", "heart", "search", "edit", "question", "alert", "sync", "share", "gear", "download", "upload")
    },
    "exclamation": ("modifier", "alert"),
    "warning": ("modifier", "alert"),
    "settings": ("modifier", "gear"),
    "cog": ("modifier", "gear"),
    "time": ("modifier", "clock"),
    "history": ("modifier", "clock"),
    "pen": ("modifier", "edit"),
    "pencil": ("modifier", "edit"),
    **{t: ("repetition", "plural") for t in ("multiple", "stack", "stacked", "group", "copy", "duplicate", "many", "double", "triple", "dual")},
    "dot": ("modifier", "dot"),
    "notification": ("modifier", "dot"),
}
# Words that join two elements ("car-to-house"); they are not elements themselves.
CONNECTORS = {"and", "with", "to", "vs", "versus", "or", "into", "from", "on", "in"}
# Unicode precedents for operators a font could implement as combining marks
UNICODE_MARKS = {"slash": "U+0338", "circle": "U+20DD", "square": "U+20DE", "diamond": "U+20DF", "prohibition": "U+20E0", "triangle": "U+20E4"}


@dataclass
class NamePart:
    role: str
    label: str
    count: int = 1


@dataclass
class NameEvidence:
    base: list[str] = field(default_factory=list)
    parts: list[NamePart] = field(default_factory=list)
    frame: str | None = None
    connector: bool = False
    text: bool = False  # letters/digits drawn as part of the glyph ("1k", "4g", "counter-5")


def name_evidence(name: str, tags: list[str] | None = None) -> NameEvidence:
    tokens = [t for t in split_name(name) if t not in STYLE and t not in SIZES]
    ev = NameEvidence()
    ev.connector = any(t in CONNECTORS for t in tokens)
    tokens = [t for t in tokens if t not in CONNECTORS]
    ops = [t for t in tokens if t in OPERATORS]
    base = [t for t in tokens if t not in OPERATORS]
    if not base:  # "circle", "plus": the (first) operator word is itself the element
        base, ops = tokens[:1], ops[1:]
    ev.base = base
    # Code-like tokens are text drawn as paths (renderers see no <text>).
    ev.text = any(re.fullmatch(r"\d+[a-z]{0,2}|[a-z]\d+", t) for t in base)
    seen: set[tuple[str, str]] = set()
    for t in ops:
        role, label = OPERATORS[t]
        if (role, label) in seen:
            continue
        seen.add((role, label))
        ev.parts.append(NamePart(role, label))
        if role == "frame":
            ev.frame = label
    return ev
