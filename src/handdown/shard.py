"""Split work across machines (GitHub Actions runners) and merge it back.

- ``export_sources``: accepted sources of one adapter as JSONL (metadata only),
  committed to the private repository as the job list.
- ``import_sources``: a runner loads its share (``--shard i/n``).
- ``export_shard``: a runner packs everything it harvested and measured
  (sources, pictograms, raw SVG, normalized SVG, features, ratings, errors).
- ``import_shard``: the local catalog merges a shard; pictograms are matched by
  (source_id, original_id), so re-importing is idempotent.
"""

from __future__ import annotations

import base64
import io
import json
import sqlite3
import tarfile
import zlib
from pathlib import Path
from typing import Any

from . import db
from .config import Config

SOURCE_COLS = (
    "id",
    "platform_id",
    "name",
    "url",
    "license_spdx",
    "license_url",
    "author",
    "author_url",
    "designer",
    "year",
    "version",
    "accessed_at",
    "found_via",
    "harvest_status",
    "adapter",
    "adapter_args",
    "system",
    "region",
    "domain",
    "category",
    "grid_size",
    "stats",
    "popularity",
    "notes",
)
SKIP_PICTO = {"id", "duplicate_of", "norm_path"}


def export_sources(conn: sqlite3.Connection, adapter: str, statuses: tuple[str, ...] = ("accepted",)) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {', '.join(SOURCE_COLS)} FROM source WHERE adapter=? AND harvest_status IN ({','.join('?' * len(statuses))}) ORDER BY id",
        (adapter, *statuses),
    ).fetchall()
    return [dict(r) for r in rows]


def import_sources(conn: sqlite3.Connection, rows: list[dict[str, Any]], shard: int = 0, shards: int = 1) -> int:
    n = 0
    for i, row in enumerate(rows):
        if i % shards != shard:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO platform (id, name, first_seen) VALUES (?, ?, ?)",
            (row["platform_id"], row["platform_id"], db.now()[:10]),
        )
        db.upsert(conn, "source", {k: row.get(k) for k in SOURCE_COLS}, ("id",))
        n += 1
    conn.commit()
    return n


def export_shard(conn: sqlite3.Connection, cfg: Config, out: Path) -> int:
    """Pack all harvested sources of this (runner-local) catalog."""
    out.parent.mkdir(parents=True, exist_ok=True)
    sources = [dict(r) for r in conn.execute(f"SELECT {', '.join(SOURCE_COLS)} FROM source WHERE harvest_status != 'accepted'")]
    platforms = [dict(r) for r in conn.execute("SELECT * FROM platform")]
    picto_cols = [r[1] for r in conn.execute("PRAGMA table_info(pictogram)") if r[1] not in SKIP_PICTO]
    lines = []
    shas: set[str] = set()
    for p in conn.execute(f"SELECT id, {', '.join(picto_cols)} FROM pictogram"):
        pid = p["id"]
        rec = {k: p[k] for k in picto_cols}
        raw = conn.execute("SELECT svg FROM raw_svg WHERE pictogram_id=?", (pid,)).fetchone()
        feat = conn.execute("SELECT vec FROM feature WHERE pictogram_id=?", (pid,)).fetchone()
        rec["_raw"] = raw[0] if raw else None
        rec["_feature"] = base64.b64encode(feat[0]).decode() if feat else None
        rec["_ratings"] = [
            dict(r)
            for r in conn.execute(
                "SELECT metric, value, detail, method, method_version, model, run_id, computed_at FROM rating WHERE pictogram_id=? AND is_override=0", (pid,)
            )
        ]
        if p["sha256"]:
            shas.add(p["sha256"])
        lines.append(json.dumps(rec, ensure_ascii=False))
    errors = [dict(r) for r in conn.execute("SELECT source_id, item, stage, error, at FROM harvest_error")]
    manifest = {"sources": sources, "platforms": platforms, "errors": errors, "count": len(lines), "exported_at": db.now()}
    with tarfile.open(out, "w:gz") as tar:
        _add(tar, "manifest.json", json.dumps(manifest, ensure_ascii=False).encode())
        _add(tar, "pictograms.jsonl.z", zlib.compress("\n".join(lines).encode(), 6))
        for sha in sorted(shas):
            path = cfg.norm_path(sha)
            if path.exists():
                tar.add(path, arcname=f"norm/{sha[:2]}/{sha}.svg")
    return len(lines)


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def import_shard(conn: sqlite3.Connection, cfg: Config, path: Path) -> int:
    with tarfile.open(path) as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())  # type: ignore[union-attr]
        for p in manifest["platforms"]:
            conn.execute("INSERT OR IGNORE INTO platform (id, name, first_seen) VALUES (?, ?, ?)", (p["id"], p["name"], p.get("first_seen")))
            db.upsert(conn, "platform", {k: v for k, v in p.items()}, ("id",))
        for s in manifest["sources"]:
            db.upsert(conn, "source", s, ("id",))
        for m in tar.getmembers():
            if m.isfile() and m.name.startswith("norm/") and ".." not in m.name and m.name.endswith(".svg"):
                sha = Path(m.name).stem
                dest = cfg.norm_path(sha)
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(tar.extractfile(m).read())  # type: ignore[union-attr]
        data = zlib.decompress(tar.extractfile("pictograms.jsonl.z").read()).decode()  # type: ignore[union-attr]
    n = 0
    for line in data.splitlines():
        if not line:
            continue
        rec = json.loads(line)
        raw, feat, ratings = rec.pop("_raw"), rec.pop("_feature"), rec.pop("_ratings")
        if rec.get("sha256"):
            rec["norm_path"] = str(cfg.norm_path(rec["sha256"]))
        cols = list(rec)
        cur = conn.execute(
            f"""INSERT INTO pictogram ({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))})
                ON CONFLICT (source_id, original_id) DO UPDATE SET {", ".join(f"{c}=excluded.{c}" for c in cols)}
                RETURNING id""",
            [rec[c] for c in cols],
        )
        pid = cur.fetchone()[0]
        if raw is not None:
            conn.execute("INSERT OR REPLACE INTO raw_svg VALUES (?, ?)", (pid, raw))
        if feat is not None:
            conn.execute("INSERT OR REPLACE INTO feature VALUES (?, ?)", (pid, base64.b64decode(feat)))
        for r in ratings:
            conn.execute(
                """INSERT OR REPLACE INTO rating (pictogram_id, metric, value, detail, method, method_version, model, run_id, computed_at, is_override)
                   VALUES (?,?,?,?,?,?,?,?,?,0)""",
                (pid, r["metric"], r["value"], r["detail"], r["method"], r["method_version"], r["model"], r["run_id"], r["computed_at"]),
            )
        n += 1
        if n % 2000 == 0:
            conn.commit()
    for e in manifest["errors"]:
        conn.execute(
            "INSERT INTO harvest_error (source_id, item, stage, error, at) VALUES (?,?,?,?,?)", (e["source_id"], e["item"], e["stage"], e["error"], e["at"])
        )
    conn.commit()
    return n
