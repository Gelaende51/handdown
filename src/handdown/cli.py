"""handdown command line."""

from __future__ import annotations

import typer

from . import db, pipeline, registry
from .config import Config

app = typer.Typer(help="Pictogram research catalog.", no_args_is_help=True)


def _conn(cfg: Config):
    return db.connect(cfg.db_path)


@app.command()
def init() -> None:
    """Create the database and load the source registry."""
    cfg = Config()
    conn = _conn(cfg)
    n = registry.sync(conn, cfg.registry)
    typer.echo(f"database at {cfg.db_path}; {n} registry entries loaded")


@app.command()
def harvest(
    adapter: str,
    only: list[str] = typer.Option(None, "--only", help="source ids or set prefixes"),
    status: list[str] = typer.Option(None, "--status", help="source statuses to harvest (default: accepted)"),
    no_registry: bool = typer.Option(False, "--no-registry", help="do not load sources.yaml (runners use a job list)"),
) -> None:
    """Fetch sources through an adapter (iconify, git-svg, npm-svg, font, commons)."""
    cfg = Config()
    conn = _conn(cfg)
    if not no_registry:
        registry.sync(conn, cfg.registry)
    ad = registry.adapter(adapter, cfg, conn)
    if hasattr(ad, "fetch"):
        typer.echo(f"fetch: {ad.fetch()}")
    counts = pipeline.harvest(conn, ad, only, tuple(status) if status else None, log=typer.echo)
    typer.echo(f"harvested {sum(counts.values())} items from {len(counts)} sources")


@app.command()
def process(workers: int = 4, limit: int = typer.Option(None), source: str = typer.Option(None)) -> None:
    """Normalize, render and measure everything not yet measured."""
    cfg = Config()
    conn = _conn(cfg)

    def progress(ok: int, fail: int) -> None:
        typer.echo(f"  {ok} ok, {fail} failed", err=True)

    ok, fail = pipeline.process(conn, cfg, workers=workers, limit=limit, source=source, progress=progress)
    typer.echo(f"processed {ok}, failed {fail}")


@app.command()
def dedupe() -> None:
    """Link exact duplicates across and within sources."""
    cfg = Config()
    typer.echo(f"{pipeline.dedupe(_conn(cfg))} duplicates linked")


@app.command()
def discover(limit: int = typer.Option(None), no_mine: bool = False) -> None:
    """Search GitHub and npm for new sources, mine READMEs for links."""
    from . import discover as d

    n = d.run(_conn(Config()), limit=limit, mine=not no_mine, log=typer.echo)
    typer.echo(f"{n} new candidate sources")


@app.command()
def triage() -> None:
    """Accept or reject candidate sources (heuristic, reasons in notes)."""
    from . import triage as t

    typer.echo(t.run(_conn(Config()), log=typer.echo))


@app.command()
def concepts(only_missing: bool = typer.Option(False, "--only-missing", help="keep assignments, handle new pictograms only")) -> None:
    """Assign concepts (meanings) from names and aliases via WordNet."""
    from . import concepts as c

    conn = _conn(Config())
    n, k = c.run(conn, progress=lambda i: typer.echo(f"  {i}", err=True), only_missing=only_missing)
    typer.echo(f"{n} pictograms mapped to {k} concepts")


@app.command()
def cluster() -> None:
    """Group each concept's pictograms into depiction clusters."""
    from . import cluster as cl

    conn = _conn(Config())
    n = cl.run(conn, progress=lambda i, t: typer.echo(f"  {i}/{t}", err=True))
    typer.echo(f"{n} depiction clusters")


@app.command()
def score() -> None:
    """Compute catalog-wide scores and the combined score."""
    from . import score as sc

    sc.run(_conn(Config()), log=typer.echo)


@app.command()
def ai(
    limit: int = typer.Option(48, help="max items to assess in this run"),
    min_sources: int = 2,
    job: str = typer.Option("rating", help="rating | composition | hierarchy"),
    sample: int = typer.Option(0, help="composition: extra random sample size"),
) -> None:
    """Headless claude -p passes: blind cluster rating, or composition conflicts + sample."""
    from . import ai as a
    from . import score as sc

    cfg = Config()
    conn = _conn(cfg)
    if job == "composition":
        typer.echo(a.run_composition(conn, cfg.data / "ai-work", limit=limit, sample=sample, log=typer.echo))
        return
    if job == "hierarchy":
        typer.echo(a.run_hierarchy(conn, cfg.data / "ai-work", limit=limit, log=typer.echo))
        return
    typer.echo(a.run(conn, cfg.data / "ai-work", limit=limit, min_sources=min_sources, log=typer.echo))
    sc.combined(conn)


@app.command()
def sync() -> None:
    """Read overrides and notes back from the vault."""
    from . import vault

    cfg = Config()
    typer.echo(f"{vault.sync(_conn(cfg), cfg, log=typer.echo)} overrides synced")


@app.command()
def export(target: str = typer.Argument(..., help="vault | html"), min_sources: int = 2) -> None:
    """Generate the vault or the static HTML site."""
    cfg = Config()
    conn = _conn(cfg)
    if target == "vault":
        from . import vault

        vault.sync(conn, cfg, log=typer.echo)
        n = vault.Exporter(conn, cfg, min_sources=min_sources).run(log=typer.echo)
        typer.echo(f"vault: {n} concept notes in {cfg.vault}")
    elif target == "html":
        from . import site

        n = site.export(conn, cfg, min_sources=min_sources, log=typer.echo)
        typer.echo(f"html: {n} concept pages in {cfg.site}")
    else:
        raise typer.BadParameter("target must be vault or html")


@app.command("export-sources")
def export_sources_cmd(
    adapter: str,
    out: str,
    status: list[str] = typer.Option(None, "--status"),
    pending: bool = typer.Option(False, "--pending", help="only sources with unmeasured pictograms"),
) -> None:
    """Write the job list for GitHub Actions (accepted sources of an adapter, JSONL)."""
    import json
    from pathlib import Path

    from . import shard

    rows = shard.export_sources(_conn(Config()), adapter, tuple(status) if status else ("accepted",), pending)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    typer.echo(f"{len(rows)} sources -> {out}")


@app.command("import-sources")
def import_sources_cmd(path: str, shard_spec: str = typer.Option("0/1", "--shard", help="i/n: take every n-th source from i")) -> None:
    """Load a job list (on a runner)."""
    import json
    from pathlib import Path

    from . import shard

    i, n = (int(x) for x in shard_spec.split("/"))
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
    typer.echo(f"{shard.import_sources(_conn(Config()), rows, i, n)} sources loaded")


@app.command("export-shard")
def export_shard_cmd(
    out: str,
    recipient: list[str] = typer.Option(None, "--recipient", help="age public key (or env HANDDOWN_AGE_RECIPIENT)"),
) -> None:
    """Pack this catalog's harvested pictograms, age-encrypted, for merging elsewhere."""
    import os
    from pathlib import Path

    from . import shard

    recipients = list(recipient or []) + [r for r in os.environ.get("HANDDOWN_AGE_RECIPIENT", "").split(",") if r.strip()]
    if not recipients and os.environ.get("GITHUB_ACTIONS") == "true":
        # Artifacts of a public repository are downloadable by anyone signed in.
        raise SystemExit("refusing to export an unencrypted shard on GitHub Actions: set HANDDOWN_AGE_RECIPIENT")
    cfg = Config()
    try:
        n = shard.export_shard(_conn(cfg), cfg, Path(out), recipients or None)
    except shard.ShardError as e:
        raise SystemExit(str(e)) from e
    typer.echo(f"{n} pictograms -> {out}{'.age' if recipients and not out.endswith('.age') else ''}")


@app.command("import-shard")
def import_shard_cmd(
    paths: list[str],
    identity: str = typer.Option(None, "--identity", help="age identity file (or env HANDDOWN_AGE_IDENTITY)"),
) -> None:
    """Merge shards produced on other machines (e.g. GitHub Actions artifacts)."""
    import os
    from pathlib import Path

    from . import shard

    ident = identity or os.environ.get("HANDDOWN_AGE_IDENTITY")
    cfg = Config()
    conn = _conn(cfg)
    for p in paths:
        try:
            n = shard.import_shard(conn, cfg, Path(p), Path(ident).expanduser() if ident else None)
        except shard.ShardError as e:
            raise SystemExit(str(e)) from e
        typer.echo(f"{p}: {n} pictograms merged")


@app.command()
def keygen(path: str = typer.Argument("~/.config/handdown/age.key")) -> None:
    """Create the age key pair for shards (run on the host; keep the file private)."""
    from pathlib import Path

    from . import shard

    try:
        pub = shard.keygen(Path(path).expanduser())
    except shard.ShardError as e:
        raise SystemExit(str(e)) from e
    typer.echo(f"private key: {path} (never commit or upload it)")
    typer.echo(f"public key:  {pub}")
    typer.echo("store the public key as repository variable HANDDOWN_AGE_RECIPIENT")


@app.command()
def errors(annotate: bool = typer.Option(False, "--annotate", help="print as GitHub Actions warning annotations")) -> None:
    """Harvest/process errors grouped by source and stage."""
    conn = _conn(Config())
    rows = conn.execute("SELECT source_id, stage, COUNT(*), MIN(error) FROM harvest_error GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40").fetchall()
    failed = conn.execute("SELECT id, harvest_status, notes FROM source WHERE harvest_status IN ('failed', 'rejected')").fetchall()

    def esc(m: str) -> str:  # workflow-command escaping
        return m.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")

    for sid, stage, n, err in rows:
        msg = f"{sid} [{stage}] {n}x: {err[:300]}"
        typer.echo(f"::warning title=harvest error::{esc(msg)}" if annotate else msg)
    for sid, st, notes in failed:
        msg = f"{sid}: {st} {(notes or '')[-200:]}"
        typer.echo(f"::notice title=source {st}::{esc(msg)}" if annotate else msg)


@app.command("wikidata-fetch")
def wikidata_fetch(out: str = typer.Argument("work/wikidata/wordnet31.jsonl")) -> None:
    """Items with a WordNet 3.1 id and their labels (needs wikidata.org; runs on Actions)."""
    from pathlib import Path

    from . import wikidata

    typer.echo(f"{wikidata.fetch(Path(out), log=typer.echo)} records -> {out}")


@app.command("wikidata-apply")
def wikidata_apply(path: str = typer.Argument("work/wikidata/wordnet31.jsonl")) -> None:
    """Link WordNet concepts to Wikidata QIDs and add missing-language labels."""
    from pathlib import Path

    from . import wikidata

    wikidata.apply(_conn(Config()), Path(path), log=typer.echo)


@app.command()
def compose(limit: int = typer.Option(None), workers: int = 1) -> None:
    """Classify composite pictograms (elements, operators, relations, sizes)."""
    from .composition import run as comp

    cfg = Config()
    typer.echo(comp.run(_conn(cfg), cfg, limit=limit, workers=workers, log=typer.echo))


@app.command()
def hierarchy() -> None:
    """Build style groups, depictions, objects and meanings."""
    from .hierarchy import group as hg

    typer.echo(hg.run(_conn(Config()), log=typer.echo))


@app.command()
def status() -> None:
    """Counts per stage and source status."""
    conn = _conn(Config())
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    typer.echo(f"platforms:   {q('SELECT COUNT(*) FROM platform')}")
    for row in conn.execute("SELECT harvest_status, COUNT(*) FROM source GROUP BY 1 ORDER BY 2 DESC"):
        typer.echo(f"sources {row[0]:<16} {row[1]}")
    typer.echo(f"pictograms:  {q('SELECT COUNT(*) FROM pictogram')}")
    typer.echo(f"  measured:  {q('SELECT COUNT(*) FROM pictogram WHERE measured_at IS NOT NULL')}")
    typer.echo(f"  unique:    {q('SELECT COUNT(*) FROM pictogram WHERE sha256 IS NOT NULL AND duplicate_of IS NULL')}")
    for row in conn.execute("SELECT color_class, COUNT(*) FROM pictogram WHERE color_class IS NOT NULL GROUP BY 1"):
        typer.echo(f"  color {row[0]:<10} {row[1]}")
    typer.echo(f"concepts:    {q('SELECT COUNT(*) FROM concept')}")
    typer.echo(f"clusters:    {q('SELECT COUNT(*) FROM depiction_cluster')}")
    typer.echo(f"errors:      {q('SELECT COUNT(*) FROM harvest_error')}")


if __name__ == "__main__":
    app()
