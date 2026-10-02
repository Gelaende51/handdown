"""handdown command line."""

from __future__ import annotations

import typer

from . import db, pipeline, registry
from .config import Config

app = typer.Typer(help="Pictogram research catalog.", no_args_is_help=True, pretty_exceptions_enable=False)


def annotate_exception(exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
    """On GitHub Actions, print an uncaught exception as an error annotation:
    job logs of this repository are not readable from the dev container, but
    annotations are served by the API."""
    import os
    import sys
    import traceback

    if os.environ.get("GITHUB_ACTIONS") == "true":
        text = "".join(traceback.format_exception(exc_type, exc, tb))[-3000:]
        text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title={exc_type.__name__}::{text}", flush=True)
    traceback.print_exception(exc_type, exc, tb, file=sys.stderr)


def main() -> None:
    """Entry point. Typer's standalone mode would catch exceptions, print them
    and exit 1, hiding them from the Actions annotation; run non-standalone
    and handle click's own exits here."""
    import sys

    try:
        rv = app(standalone_mode=False)
    except Exception as e:
        # typer vendors its own click: match its exceptions by interface
        name = type(e).__name__
        if name == "Exit" and hasattr(e, "exit_code"):
            sys.exit(e.exit_code)
        if hasattr(e, "show") and hasattr(e, "exit_code"):
            e.show()
            sys.exit(e.exit_code)
        if name == "Abort":
            sys.exit(1)
        annotate_exception(*sys.exc_info())
        sys.exit(1)
    # non-standalone click returns the code of typer.Exit instead of raising it
    if isinstance(rv, int) and rv != 0:
        sys.exit(rv)


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
    workers: int = typer.Option(1, help="hierarchy: parallel model calls"),
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
        out = a.run_hierarchy(conn, cfg.data / "ai-work", limit=limit, workers=workers, log=typer.echo)
        typer.echo(out)
        if out.get("quota_hit"):
            raise typer.Exit(75)  # EX_TEMPFAIL: the pacing loop waits for the reset
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
def hierarchy(reresolve: bool = typer.Option(False, "--reresolve", help="only re-derive AI objects from stored answers")) -> None:
    """Build style groups, depictions, objects and meanings."""
    from .hierarchy import group as hg

    conn = _conn(Config())
    if reresolve:
        typer.echo(f"{hg.reresolve_ai_objects(conn)} AI objects re-resolved")
        return
    typer.echo(hg.run(conn, log=typer.echo))


@app.command("vision-vocab")
def vision_vocab(out: str = typer.Argument("work/vision/labels.jsonl")) -> None:
    """Write the label vocabulary (objects, views, features) for the vision runners."""
    import json
    from pathlib import Path

    from . import vision

    labels = vision.build_vocabulary(_conn(Config()))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text("".join(json.dumps(lab, ensure_ascii=False) + "\n" for lab in labels))
    typer.echo(f"{len(labels)} labels -> {out}")


@app.command()
def embed(labels: str = "work/vision/labels.jsonl", batch: int = 64) -> None:
    """DINOv2 embeddings + SigLIP labels for every pictogram (runners: needs torch)."""
    import json
    from pathlib import Path

    from . import vision

    cfg = Config()
    labs = [json.loads(line) for line in Path(labels).read_text().splitlines() if line]
    n = vision.embed(_conn(cfg), cfg, labs, vision.TorchModels(labs), batch=batch, log=typer.echo)
    typer.echo(f"{n} pictograms embedded")


@app.command("export-vision")
def export_vision_cmd(out: str, recipient: list[str] = typer.Option(None, "--recipient")) -> None:
    """Write embeddings and labels (age-encrypted with HANDDOWN_AGE_RECIPIENT)."""
    import os

    from . import vision

    recipients = list(recipient or []) + [r for r in os.environ.get("HANDDOWN_AGE_RECIPIENT", "").split(",") if r.strip()]
    if not recipients and os.environ.get("GITHUB_ACTIONS") == "true":
        raise SystemExit("refusing to export unencrypted vision data on GitHub Actions: set HANDDOWN_AGE_RECIPIENT")
    typer.echo(f"{vision.export_vision(_conn(Config()), out, recipients or None)} pictograms -> {out}")


@app.command("import-vision")
def import_vision_cmd(paths: list[str], identity: str = typer.Option(None, "--identity")) -> None:
    """Merge vision shards (embeddings and labels)."""
    import os

    from . import vision

    ident = identity or os.environ.get("HANDDOWN_AGE_IDENTITY")
    cfg = Config()
    conn = _conn(cfg)
    for p in paths:
        typer.echo(f"{p}: {vision.import_vision(conn, cfg, p, os.path.expanduser(ident) if ident else None)}")


@app.command("vision-score")
def vision_score() -> None:
    """Re-score labels locally (softmax) from stored image and label embeddings."""
    from . import vision

    typer.echo(f"{vision.score_labels(_conn(Config()), log=typer.echo)} pictograms scored")


@app.command("vision-train")
def vision_train(min_examples: int = 8, cv: bool = True) -> None:
    """Train the object probe on Claude-assessed depictions (data/vision-probe.npz)."""
    from . import vision

    cfg = Config()
    typer.echo(vision.train_probe(_conn(cfg), cfg.data / "vision-probe.npz", min_examples=min_examples, cv=cv))


@app.command("vision-predict")
def vision_predict(min_conf: float = 0.9) -> None:
    """Set objects of depictions without one where the probe is confident."""
    from . import vision

    cfg = Config()
    typer.echo(vision.predict_probe(_conn(cfg), cfg.data / "vision-probe.npz", min_conf=min_conf))


@app.command("vision-apply")
def vision_apply(min_score: float = 0.3) -> None:
    """Fill missing objects and views of depictions from vision labels."""
    from . import vision

    typer.echo(vision.apply(_conn(Config()), min_score=min_score))


def _jsonl(path: str) -> list[dict]:
    import json
    from pathlib import Path

    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


@app.command("bench-sample")
def bench_sample(n: int = 500, per_source: int = 4, seed: int = 0, out: str = "work/bench") -> None:
    """Draw the vision benchmark sample (sample.jsonl and the sources.jsonl job list)."""
    import json
    from pathlib import Path

    from . import bench

    sample, sources = bench.make_sample(_conn(Config()), n=n, per_source=per_source, seed=seed)
    Path(out).mkdir(parents=True, exist_ok=True)
    for name, rows in (("sample.jsonl", sample), ("sources.jsonl", sources)):
        (Path(out) / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    typer.echo(f"{len(sample)} pictograms from {len(sources)} sources -> {out}")


@app.command("bench-prepare")
def bench_prepare(sample: str) -> None:
    """Runner: leave only the sample's pictograms for `process`."""
    from . import bench

    typer.echo(f"{bench.prepare(_conn(Config()), _jsonl(sample))} pictograms outside the sample skipped")


@app.command("bench-run")
def bench_run(
    sample: str,
    model: str = typer.Option(..., help="ollama:<tag>[@size=128,threads=4] | florence:omniparser"),
    out: str = "bench.jsonl",
    limit: int = typer.Option(None, help="only the first N sample keys (speed tests)"),
    present_only: bool = typer.Option(False, help="skip items of sources this database lacks (sharded runs)"),
    variants: str = typer.Option("norm", help="norm, original or norm,original (raster originals in colour too)"),
) -> None:
    """Runner: ask one model to name the object of every sample pictogram."""
    from pathlib import Path

    from . import bench

    cfg = Config()
    conn = _conn(cfg)
    items = bench.present_only(conn, _jsonl(sample)) if present_only else _jsonl(sample)
    ask, batch, size = bench.backend(model)
    typer.echo(bench.run(conn, cfg, items, ask, model, Path(out), batch=batch, size=size, limit=limit, variants=tuple(variants.split(","))))


@app.command("bench-score")
def bench_score(results: list[str]) -> None:
    """Compare benchmark answers with Claude's objects, per model."""
    from . import bench

    rows = [r for path in results for r in _jsonl(path)]
    report = bench.score(_conn(Config()), rows)
    for model, m in sorted(report.items(), key=lambda kv: -(kv[1]["exact"] + kv[1]["near"])):
        typer.echo(f"{model:36} n={m['n']:4}  exact {m['exact']:.0%}  +near {m['exact'] + m['near']:.0%}  {m['seconds']:.1f} s/image")
    for model, m in report.items():
        typer.echo(f"\n{model} misses: " + "; ".join(f"{g[3:]} <- {a!r}" for g, a in m["misses"]))


@app.command("bench-text-items")
def bench_text_items(n: int = 2000, seed: int = 0, out: str = "work/bench/text.jsonl") -> None:
    """Text benchmark items: names of Claude-assessed depictions and candidate objects (metadata only)."""
    import json
    from pathlib import Path

    from . import bench

    items = bench.text_items(_conn(Config()), n=n, seed=seed)
    Path(out).write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))
    typer.echo(f"{len(items)} items -> {out}")


@app.command("bench-text")
def bench_text(items: str, out: str = "text.jsonl", batch: int = 32) -> None:
    """Runner: Laya picks the drawn object among each item's candidates."""
    from pathlib import Path

    from . import bench

    typer.echo(f"{bench.run_text(_jsonl(items), bench.LayaChoice(), 'laya', Path(out), batch=batch)} answered")


@app.command("bench-text-score")
def bench_text_score(items: str, results: list[str] = typer.Argument(None)) -> None:
    """Compare text answers (and the name rules) with Claude's objects."""
    from . import bench

    rows = [r for path in results or [] for r in _jsonl(path)]
    for model, m in bench.score_text(_conn(Config()), _jsonl(items), rows).items():
        typer.echo(
            f"{model:10} n={m['n']:5}  exact {m['exact']:.0%}  (answerable {m['coverage']:.0%})"
            f"  p>=0.9: {m['share_at_0.9']:.0%} of items, {m['exact_at_0.9']:.0%} correct"
        )


@app.command("bench-claude")
def bench_claude(
    sample: str,
    model: str = typer.Option("sonnet"),
    effort: str = typer.Option(None, help="low | medium | high | max; reasoning off if not given"),
    out: str = typer.Option(...),
    limit: int = typer.Option(None),
    batch: int = 24,
) -> None:
    """Claude on the benchmark sample, as in production (sheets of 24); logs tokens and list-price cost per image."""
    from pathlib import Path

    from . import bench

    cfg = Config()
    typer.echo(bench.run_claude(_conn(cfg), cfg, _jsonl(sample), model, effort, Path(out), batch=batch, limit=limit))


@app.command("vlm-jobs")
def vlm_jobs(out: str = "work/vlm") -> None:
    """Job lists for labelling depictions without an object on runners (items-/sources-<adapter>.jsonl)."""
    import json
    from pathlib import Path

    from . import bench

    Path(out).mkdir(parents=True, exist_ok=True)
    for adapter, (items, sources) in bench.vlm_jobs(_conn(Config())).items():
        for name, rows in ((f"items-{adapter}.jsonl", items), (f"sources-{adapter}.jsonl", sources)):
            (Path(out) / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        typer.echo(f"{adapter}: {len(items)} depictions from {len(sources)} sources")


@app.command("vlm-slice")
def vlm_slice(
    items: str, sources: str, shard: str = typer.Option(..., help="i/N"), out_items: str = "slice-items.jsonl", out_sources: str = "slice-sources.jsonl"
) -> None:
    """Runner: this shard's contiguous share of the items and the job list of just their sources."""
    import json
    from pathlib import Path

    from . import bench

    i, n = (int(x) for x in shard.split("/"))
    part, need = bench.vlm_slice(_jsonl(items), _jsonl(sources), i, n)
    for path, rows in ((out_items, part), (out_sources, need)):
        Path(path).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    typer.echo(f"shard {shard}: {len(part)} depictions from {len(need)} sources")


@app.command("vlm-backup")
def vlm_backup(model: str = "opus", effort: str = typer.Option("high", help="off for no reasoning"), limit: int = typer.Option(None)) -> None:
    """Claude names the objects the vision model left (unresolvable or missing); exits 75 at the usage limit."""
    from . import ai, bench

    cfg = Config()
    try:
        typer.echo(bench.vlm_backup(_conn(cfg), cfg, model, None if effort == "off" else effort, limit=limit))
    except ai.QuotaExceeded as e:
        typer.echo(f"usage limit reached, stopping: {e}")
        raise typer.Exit(75) from e  # EX_TEMPFAIL: run again after the reset


@app.command("raster-disagreements")
def raster_disagreements(out: str = "data/raster-disagreements.jsonl") -> None:
    """Raster pictograms named differently in colour and in black and white (the 1-bit version lost meaning)."""
    import json
    from pathlib import Path

    from . import bench

    rows = bench.raster_disagreements(_conn(Config()))
    Path(out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    near = sum(r["near"] for r in rows)
    typer.echo(f"{len(rows)} disagreements ({near} near, {len(rows) - near} different objects) -> {out}")
    for r in [r for r in rows if not r["near"]][:15]:
        typer.echo(f"  {r['source_id']} {r['original_id']}: colour {r['original_raw']!r}, black and white {r['monochrome_raw']!r}")


@app.command("recheck-rasters")
def recheck_rasters(min_files: int = 20, adapter: str = typer.Option("git-svg", help="git-svg (GitHub tree API) or npm-svg (package tarball)")) -> None:
    """Accept repositories or packages rejected for too few SVGs when they hold raster icon sets."""
    import io
    import os
    import subprocess
    import tarfile

    import httpx

    from . import triage

    if adapter == "npm-svg":
        client = httpx.Client(timeout=120, follow_redirects=True)

        def fetch_tree(package: str) -> list[dict]:
            meta = client.get(f"https://registry.npmjs.org/{package}/latest")
            meta.raise_for_status()
            dist = meta.json().get("dist", {})
            if (dist.get("unpackedSize") or 0) > 150 * 1024 * 1024:
                raise ValueError("package larger than 150 MB")
            data = client.get(dist["tarball"])
            data.raise_for_status()
            with tarfile.open(fileobj=io.BytesIO(data.content)) as tar:  # listed, not extracted
                return [{"path": m.name, "type": "blob", "size": m.size} for m in tar if m.isfile()]

    else:
        token = os.environ.get("GH_TOKEN") or subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
        # renamed repositories redirect
        client = httpx.Client(headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}, timeout=60, follow_redirects=True)

        def fetch_tree(repo: str) -> list[dict]:
            r = client.get(f"https://api.github.com/repos/{repo}/git/trees/HEAD", params={"recursive": "1"})
            r.raise_for_status()
            return r.json().get("tree", [])

    typer.echo(triage.recheck_rasters(_conn(Config()), fetch_tree, min_files=min_files, adapter=adapter))


@app.command("off-topic")
def off_topic(source: str, patterns: list[str], reason: str = typer.Option(None, help="why; leave out to put them on topic again")) -> None:
    """Mark a source's pictograms (original ids matching glob patterns) as off-topic reference, kept but not labelled or exported."""
    from . import topic

    n = topic.mark(_conn(Config()), source, patterns, reason)
    typer.echo(f"{n} pictograms of {source} {'off-topic: ' + reason if reason else 'on topic'}")


@app.command("vlm-apply")
def vlm_apply(answers: list[str]) -> None:
    """Set objects of depictions without one from vision model answers (method 'vlm')."""
    from pathlib import Path

    from . import bench

    conn = _conn(Config())
    for path in answers:  # the file names the run (work/vlm/answers/<adapter>-<run id>.jsonl)
        typer.echo(f"{path}: {bench.vlm_apply(conn, _jsonl(path), run=Path(path).stem)}")


@app.command("provenance-backfill")
def provenance_backfill(bench_dir: str = "work/bench") -> None:
    """Log the origin of classifications made before the log existed (Claude, probe, benchmarks)."""
    from pathlib import Path

    from . import provenance

    typer.echo(provenance.backfill(_conn(Config()), Path(bench_dir)))


@app.command()
def serve(port: int = 8765) -> None:
    """Review app: browse the hierarchy and mark classification errors (http://127.0.0.1:PORT)."""
    from . import review

    server = review.make_server(Config(), port=port)
    typer.echo(f"handdown review app on http://127.0.0.1:{server.server_address[1]}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


@app.command()
def feedback() -> None:
    """Open classification error marks per level and per method."""
    from . import review

    typer.echo(review.feedback_summary(_conn(Config())))


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
    main()
