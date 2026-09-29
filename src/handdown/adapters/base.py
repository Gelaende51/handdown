"""Adapter interface: one adapter turns one kind of source into items."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SourceInfo:
    id: str
    name: str
    platform_id: str
    url: str | None = None
    license_spdx: str | None = None
    license_url: str | None = None
    author: str | None = None
    author_url: str | None = None
    version: str | None = None
    domain: str | None = None
    category: str | None = None
    grid_size: float | None = None
    found_via: str | None = None
    notes: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Item:
    original_id: str
    svg: str
    name: str | None = None
    url: str | None = None
    raw_path: str | None = None
    format: str = "svg"
    tags: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    description: str | None = None
    unicode_codepoint: str | None = None
    hidden: bool = False


class Adapter(Protocol):
    name: str

    def sources(self) -> Iterator[SourceInfo]:
        """Sources this adapter can harvest (one adapter may cover many sets)."""
        ...

    def items(self, source_id: str) -> Iterator[Item]: ...
