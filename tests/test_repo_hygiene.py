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


def test_actions_excepthook_emits_error_annotation(monkeypatch, capsys):
    from handdown import cli

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    try:
        raise ValueError("boom\nsecond line")
    except ValueError as e:
        cli.annotate_exception(type(e), e, e.__traceback__)
    out = capsys.readouterr().out
    assert out.startswith("::error title=ValueError::") and "boom%0Asecond line" in out


def test_cli_main_annotates_command_errors(monkeypatch, capsys):
    import pytest as _pytest

    from handdown import cli

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr("sys.argv", ["handdown", "embed", "--labels", "/nonexistent/labels.jsonl"])
    with _pytest.raises(SystemExit):
        cli.main()
    assert "::error title=FileNotFoundError::" in capsys.readouterr().out


def test_cli_usage_errors_keep_exit_code(monkeypatch, capsys):
    import pytest as _pytest

    from handdown import cli

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr("sys.argv", ["handdown", "no-such-command"])
    with _pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2 and "::error" not in capsys.readouterr().out
