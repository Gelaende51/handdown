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
) -> None:
    """Fetch sources through an adapter (iconify, git-svg, npm-svg)."""
    cfg = Config()
    conn = _conn(cfg)
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
def concepts() -> None:
    """Assign concepts (meanings) from names and aliases via WordNet."""
    from . import concepts as c

    conn = _conn(Config())
    n, k = c.run(conn, progress=lambda i: typer.echo(f"  {i}", err=True))
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
def ai(limit: int = typer.Option(48, help="max clusters to assess in this run"), min_sources: int = 2) -> None:
    """Blind + informed AI assessment of depiction clusters (headless claude -p)."""
    from . import ai as a
    from . import score as sc

    cfg = Config()
    conn = _conn(cfg)
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
