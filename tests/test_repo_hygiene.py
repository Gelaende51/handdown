"""The repository is public: harvested assets (images, fonts, archives,
shards) must never be tracked. CI runs this test too."""

import re
import subprocess
from pathlib import Path

import pytest

FORBIDDEN = re.compile(
    r"\.(svg|svgz|png|jpe?g|gif|webp|avif|bmp|ico|tiff?|eps|ai|pdf|psd"
    r"|ttf|otf|woff2?|eot|pfb|pfa"
    r"|tar|gz|tgz|zip|7z|xz|bz2|zst|rar|age|plain|sqlite|db)$",
    re.I,
)
ROOT = Path(__file__).resolve().parent.parent


def tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [f for f in out.decode().split("\0") if f]


def test_no_harvested_assets_tracked():
    bad = [f for f in tracked() if FORBIDDEN.search(f)]
    assert not bad, f"assets/archives must not be committed: {bad}"


def test_private_dirs_not_tracked():
    bad = [f for f in tracked() if f.split("/", 1)[0] in ("data", "vault", "site")]
    assert not bad, bad
