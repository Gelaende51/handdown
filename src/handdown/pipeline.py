"""Pipeline stages: harvest → process (normalize + measure) → dedupe."""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from . import db
from .adapters.base import Adapter, SourceInfo
from .config import Config
from .metrics import METHOD_VERSION, measure
from .normalize import normalize

BATCH = 500


def upsert_source(conn: sqlite3.Connection, s: SourceInfo, adapter: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO platform (id, name, found_via, first_seen) VALUES (?, ?, ?, ?)",
        (s.platform_id, s.platform_id, f"adapter:{adapter}", db.now()[:10]),
    )
    db.upsert(
        conn,
        "source",
        {
            "id": s.id,
            "platform_id": s.platform_id,
            "name": s.name,
            "url": s.url,
            "license_spdx": s.license_spdx,
            "license_url": s.license_url,
            "author": s.author,
            "author_url": s.author_url,
            "version": s.version,
            "domain": s.domain,
            "category": s.category,
            "grid_size": s.grid_size,
            "found_via": s.found_via,
            "notes": s.notes,
            "adapter": adapter,
            "adapter_args": s.extra or None,
            "accessed_at": db.now(),
        },
        ("id",),
    )


def harvest(
    conn: sqlite3.Connection, adapter: Adapter, only: Iterable[str] | None = None, statuses: tuple[str, ...] | None = None, log: Any = None
) -> dict[str, int]:
    wanted = set(only) if only else None
    counts: dict[str, int] = {}
    infos = list(adapter.sources(statuses) if statuses else adapter.sources())  # type: ignore[call-arg]
    if wanted:
        infos = [i for i in infos if i.id in wanted or i.id.split(":", 1)[-1] in wanted]
    if hasattr(adapter, "prefetch"):
        adapter.prefetch(infos)
    for k, info in enumerate(infos):
        if wanted and info.id not in wanted and info.id.split(":", 1)[-1] not in wanted:
            continue
        upsert_source(conn, info, adapter.name)
        conn.commit()  # never hold the write lock across a download
        n = 0
        try:
            for item in adapter.items(info.id):
                cur = conn.execute(
                    """INSERT INTO pictogram (source_id, original_id, original_name, original_url, raw_path,
                           format, raw_tags, raw_categories, raw_description, unicode_codepoint, harvested_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT (source_id, original_id) DO UPDATE SET
                           original_name=excluded.original_name, original_url=excluded.original_url,
                           raw_path=excluded.raw_path, raw_tags=excluded.raw_tags,
                           raw_categories=excluded.raw_categories, unicode_codepoint=excluded.unicode_codepoint,
                           harvested_at=excluded.harvested_at
                       RETURNING id""",
                    (
                        info.id,
                        item.original_id,
                        item.name,
                        item.url,
                        item.raw_path,
                        item.format,
                        db.encode(item.tags),
                        db.encode(item.categories),
                        item.description,
                        item.unicode_codepoint,
                        db.now(),
                    ),
                )
                pid = cur.fetchone()[0]
                old = conn.execute("SELECT svg FROM raw_svg WHERE pictogram_id=?", (pid,)).fetchone()
                if old is None or old[0] != item.svg:
                    conn.execute("INSERT OR REPLACE INTO raw_svg VALUES (?,?)", (pid, item.svg))
                    conn.execute("UPDATE pictogram SET normalized_at=NULL, measured_at=NULL WHERE id=?", (pid,))
                n += 1
                if n % BATCH == 0:
                    conn.commit()
            status = "harvested"
        except Exception as e:  # one broken source must not stop the others
            db.log_error(conn, info.id, "*", "harvest", repr(e))
            status = "failed"
        meta = getattr(adapter, "meta", {}).get(info.id, {})
        if meta.get("license"):
            conn.execute("UPDATE source SET license_spdx=COALESCE(license_spdx, ?) WHERE id=?", (meta["license"], info.id))
        note = None
        min_items = getattr(adapter, "min_items", 0)
        if status == "harvested" and n < min_items:
            status, note = "rejected", f"only {n} SVG files"
            conn.execute("DELETE FROM pictogram WHERE source_id=?", (info.id,))
        conn.execute(
            "UPDATE source SET harvest_status=?, stats=json_set(COALESCE(stats,'{}'), '$.items', ?),"
            " notes=CASE WHEN ? IS NULL THEN notes ELSE COALESCE(notes || ' | ', '') || ? END WHERE id=?",
            (status, n, note, note, info.id),
        )
        conn.commit()
        counts[info.id] = n
        if log:
            log(f"  [{k + 1}/{len(infos)}] {info.id}: {n} {status}{' (' + note + ')' if note else ''}")
    return counts


def _work(args: tuple[int, str, str, str | None]) -> tuple[int, dict[str, Any] | None, str | None]:
    pid, raw, norm_root, old_sha = args
    try:
        r = normalize(raw)
        sha = hashlib.sha256(r.svg.encode()).hexdigest()
        if sha == old_sha:
            return pid, {"unchanged": True}, None  # same drawing: keep measurements
        path = Path(norm_root) / sha[:2] / f"{sha}.svg"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(r.svg)
        m = measure(r.svg)
        return pid, {"norm": r, "sha": sha, "path": str(path), "m": m}, None
    except Exception as e:
        return pid, None, f"{type(e).__name__}: {e}"


def _pending_ids(conn: sqlite3.Connection, limit: int | None, source: str | None) -> list[int]:
    sql = "SELECT id FROM pictogram WHERE measured_at IS NULL"
    params: list[Any] = []
    if source:
        sql += " AND source_id=?"
        params.append(source)
    sql += " ORDER BY id"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [r[0] for r in conn.execute(sql, params)]


def process(
    conn: sqlite3.Connection, cfg: Config, workers: int = 4, limit: int | None = None, source: str | None = None, progress: Any = None, chunk: int = 4000
) -> tuple[int, int]:
    """Normalize and measure pending pictograms.

    SQLite connections are thread-bound, so raw SVGs are read here in the main
    thread, one chunk at a time, and only plain tuples go to the workers.
    """
    ids = _pending_ids(conn, limit, source)
    ok = fail = 0
    ctx = mp.get_context("forkserver")
    with ctx.Pool(workers, maxtasksperchild=2000) as pool:
        for start in range(0, len(ids), chunk):
            part = ids[start : start + chunk]
            marks = ",".join("?" * len(part))
            tasks = [
                (r[0], r[1], str(cfg.norm), r[2])
                for r in conn.execute(
                    f"SELECT r.pictogram_id, r.svg, p.sha256 FROM raw_svg r JOIN pictogram p ON p.id = r.pictogram_id WHERE r.pictogram_id IN ({marks})", part
                )
            ]
            now = db.now()
            for pid, res, err in pool.imap_unordered(_work, tasks, chunksize=32):
                if err or res is None:
                    src = conn.execute("SELECT source_id FROM pictogram WHERE id=?", (pid,)).fetchone()
                    db.log_error(conn, src[0] if src else "?", str(pid), "process", err or "no result")
                    conn.execute("UPDATE pictogram SET normalized_at=?, measured_at=?, svg_valid=0 WHERE id=?", (now, now, pid))
                    fail += 1
                elif res.get("unchanged"):
                    conn.execute("UPDATE pictogram SET normalized_at=?, measured_at=? WHERE id=?", (now, now, pid))
                    ok += 1
                else:
                    _store(conn, pid, res, now)
                    ok += 1
            conn.commit()
            if progress:
                progress(ok, fail)
    return ok, fail


def _store(conn: sqlite3.Connection, pid: int, res: dict[str, Any], now: str) -> None:
    r, m, sha = res["norm"], res["m"], res["sha"]
    conn.execute(
        """UPDATE pictogram SET norm_path=?, sha256=?, phash=?, color_class=?, color_count=?, svg_valid=1,
               uses=?, fill_rule=?, file_size=?, viewbox=?, style=?, stroke_width=?, stroke_caps=?,
               padding_ratio=?, symmetry=?, mirror_safe=?, components=?, holes=?, figure_ground_ratio=?,
               container_shape=?, node_count=?, path_count=?, has_text=?, font_ready=?,
               normalized_at=?, measured_at=?
           WHERE id=?""",
        (
            res["path"],
            sha,
            m["phash"],
            r.color_class,
            r.color_count,
            db.encode(r.uses),
            r.fill_rule,
            len(r.svg),
            " ".join(f"{v:g}" for v in r.viewbox),
            "duotone" if r.extra.get("duotone") else m["style"],
            r.stroke_width,
            r.stroke_caps,
            m["padding_ratio"],
            db.encode(m["symmetry"]),
            int(m["symmetry"]["h"]),
            m["components"],
            m["holes"],
            m["figure_ground_ratio"],
            m["container_shape"],
            r.node_count,
            r.path_count,
            int(r.has_text),
            int(not r.uses["stroke"] and not r.uses["mask"] and not r.uses["text"]),
            now,
            now,
            pid,
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO feature VALUES (?, ?)",
        (pid, np.asarray(m["feature"], dtype=np.float16).tobytes()),
    )
    details = {
        "legibility": {k: m[k] for k in ("legibility_curve", "min_feature_px16", "mud16", "hole_survival16")},
        "balance": {},
        "consistency": {k: m[k] for k in ("grey24", "stroke_cv")},
        "simplicity_raw": {
            "nodes": r.node_count,
            "paths": r.path_count,
            "components": m["components"],
            "perimetric_complexity": m["perimetric_complexity"],
            "png_bytes64": m["png_bytes64"],
            "edge_density": m["edge_density"],
        },
    }
    for metric in ("legibility", "balance", "consistency"):
        conn.execute(
            """INSERT OR REPLACE INTO rating (pictogram_id, metric, value, detail, method, method_version, computed_at, is_override)
               VALUES (?,?,?,?,?,?,?,0)""",
            (pid, metric, m[metric], json.dumps(details[metric]), "render", METHOD_VERSION, now),
        )
    conn.execute(
        """INSERT OR REPLACE INTO rating (pictogram_id, metric, value, detail, method, method_version, computed_at, is_override)
           VALUES (?,?,NULL,?,?,?,?,0)""",
        (pid, "simplicity", json.dumps(details["simplicity_raw"]), "render+percentile", METHOD_VERSION, now),
    )


def dedupe(conn: sqlite3.Connection) -> int:
    """Link exact duplicates (same normalized sha256) to the lowest id."""
    conn.execute("UPDATE pictogram SET duplicate_of=NULL")
    cur = conn.execute(
        """UPDATE pictogram SET duplicate_of = (
               SELECT MIN(p2.id) FROM pictogram p2 WHERE p2.sha256 = pictogram.sha256)
           WHERE sha256 IS NOT NULL
             AND id != (SELECT MIN(p2.id) FROM pictogram p2 WHERE p2.sha256 = pictogram.sha256)"""
    )
    conn.commit()
    return cur.rowcount
