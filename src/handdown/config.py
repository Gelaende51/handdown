"""Paths and tunables. Everything under ``data/`` is private and gitignored."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_WEIGHTS: dict[str, float] = {
    "meaning": 25,
    "depiction": 15,
    "familiarity": 10,
    "convention": 10,
    "distinctiveness": 10,
    "legibility": 15,
    "simplicity": 10,
    "balance": 2.5,
    "consistency": 2.5,
}

USER_AGENT = "handdown/0.1 (pictogram research catalog; https://github.com/Gelaende51/handdown)"


@dataclass
class Config:
    root: Path = field(default_factory=lambda: Path(os.environ.get("HANDDOWN_ROOT", ".")).resolve())

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def db_path(self) -> Path:
        return self.data / "catalog.sqlite"

    @property
    def raw(self) -> Path:
        return self.data / "raw"

    @property
    def norm(self) -> Path:
        return self.data / "norm"

    @property
    def png(self) -> Path:
        return self.data / "png"

    @property
    def cache(self) -> Path:
        return self.data / "cache"

    @property
    def vault(self) -> Path:
        return self.root / "vault"

    @property
    def site(self) -> Path:
        return self.root / "site"

    @property
    def registry(self) -> Path:
        return self.root / "sources.yaml"

    def norm_path(self, sha: str) -> Path:
        return self.norm / sha[:2] / f"{sha}.svg"
