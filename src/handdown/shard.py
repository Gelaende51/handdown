"""Split work across machines (GitHub Actions runners) and merge it back.

- ``export_sources``: accepted sources of one adapter as JSONL (metadata only),
  committed to the private repository as the job list.
- ``import_sources``: a runner loads its share (``--shard i/n``).
- ``export_shard``: a runner packs everything it harvested and measured
  (sources, pictograms, raw SVG, normalized SVG, features, ratings, errors).
- ``import_shard``: the local catalog merges a shard; pictograms are matched by
  (source_id, original_id), so re-importing is idempotent.

Shards contain local copies of pictograms under many licenses, and artifacts
of the public repository can be downloaded by any signed-in GitHub user. So
shards leave a runner only encrypted with age (``*.tar.gz.age``) to the
operator's public key; the private key stays on the host and ``import_shard``
decrypts with it.
"""

from __future__ import annotations

import base64
import gzip
import io
import json
import sqlite3
import tarfile
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


def export_sources(conn: sqlite3.Connection, adapter: str, statuses: tuple[str, ...] = ("accepted",), pending: bool = False) -> list[dict[str, Any]]:
    """Job list rows. ``pending``: only sources with pictograms not yet measured
    (they are re-harvested and processed on a runner, which is faster than
    shipping the raw files there)."""
    sql = f"SELECT {', '.join(SOURCE_COLS)} FROM source WHERE adapter=? AND harvest_status IN ({','.join('?' * len(statuses))})"
    if pending:
        sql += " AND id IN (SELECT DISTINCT source_id FROM pictogram WHERE measured_at IS NULL)"
    rows = conn.execute(sql + " ORDER BY id", (adapter, *statuses)).fetchall()
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
        # A job list is the work to do: whatever its status was locally
        # (failed, rejected, harvested), the runner harvests it again.
        db.upsert(conn, "source", {**{k: row.get(k) for k in SOURCE_COLS}, "harvest_status": "accepted"}, ("id",))
        n += 1
    conn.commit()
    return n


class ShardError(Exception):
    pass


def keygen(identity_path: Path) -> str:
    """Create an age identity file (mode 0600) and return its public key."""
    from pyrage import x25519

    if identity_path.exists():
        raise ShardError(f"{identity_path} exists; not overwriting a private key")
    ident = x25519.Identity.generate()
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    identity_path.touch(mode=0o600)
    identity_path.write_text(f"# handdown shard key, public key: {ident.to_public()}\n{ident}\n")
    return str(ident.to_public())


def _load_identity(identity_path: Path):
    from pyrage import x25519

    keys = [line.strip() for line in identity_path.read_text().splitlines() if line.startswith("AGE-SECRET-KEY-")]
    if not keys:
        raise ShardError(f"no AGE-SECRET-KEY in {identity_path}")
    return x25519.Identity.from_str(keys[0])


def export_shard(conn: sqlite3.Connection, cfg: Config, out: Path, recipients: list[str] | None = None) -> int:
    """Pack all harvested sources of this (runner-local) catalog; with
    ``recipients`` the result is age-encrypted and ``out`` gets ``.age``."""
    if recipients:
        from pyrage import x25519

        try:
            keys = [x25519.Recipient.from_str(r.strip()) for r in recipients]
        except Exception as e:
            raise ShardError(f"invalid age recipient: {e}") from e
        plain = out.with_name(out.name + ".plain")
        n = _write_shard(conn, cfg, plain)
        import pyrage

        target = out if out.name.endswith(".age") else out.with_name(out.name + ".age")
        pyrage.encrypt_file(str(plain), str(target), keys)
        plain.unlink()
        return n
    return _write_shard(conn, cfg, out)


def _write_shard(conn: sqlite3.Connection, cfg: Config, out: Path) -> int:
    """Stream records to a gzip member so memory stays flat for large shards."""
    out.parent.mkdir(parents=True, exist_ok=True)
    sources = [dict(r) for r in conn.execute(f"SELECT {', '.join(SOURCE_COLS)} FROM source WHERE harvest_status != 'accepted'")]
    platforms = [dict(r) for r in conn.execute("SELECT * FROM platform")]
    picto_cols = [r[1] for r in conn.execute("PRAGMA table_info(pictogram)") if r[1] not in SKIP_PICTO]
    shas: set[str] = set()
    n = 0
    records = out.with_name(out.name + ".records.gz")
    with gzip.open(records, "wt", encoding="utf-8", compresslevel=6) as f:
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
                    "SELECT metric, value, detail, method, method_version, model, run_id, computed_at FROM rating WHERE pictogram_id=? AND is_override=0",
                    (pid,),
                )
            ]
            if p["sha256"]:
                shas.add(p["sha256"])
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    errors = [dict(r) for r in conn.execute("SELECT source_id, item, stage, error, at FROM harvest_error")]
    manifest = {"format": 2, "sources": sources, "platforms": platforms, "errors": errors, "count": n, "exported_at": db.now()}
    try:
        with tarfile.open(out, "w:gz") as tar:
            # Order matters for single-pass reading: manifest (sources) first.
            _add(tar, "manifest.json", json.dumps(manifest, ensure_ascii=False).encode())
            tar.add(records, arcname="pictograms.jsonl.gz")
            for sha in sorted(shas):
                path = cfg.norm_path(sha)
                if path.exists():
                    tar.add(path, arcname=f"norm/{sha[:2]}/{sha}.svg")
    finally:
        records.unlink(missing_ok=True)
    return n


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def import_shard(conn: sqlite3.Connection, cfg: Config, path: Path, identity: Path | None = None) -> int:
    if path.name.endswith(".age"):
        if identity is None:
            raise ShardError(f"{path} is encrypted: pass the age identity file (--identity or HANDDOWN_AGE_IDENTITY)")
        import pyrage

        plain = cfg.cache / "shards" / path.name.removesuffix(".age")
        plain.parent.mkdir(parents=True, exist_ok=True)
        try:
            pyrage.decrypt_file(str(path), str(plain), [_load_identity(identity)])
        except pyrage.DecryptError as e:
            raise ShardError(f"cannot decrypt {path}: wrong key?") from e
        try:
            return _read_shard(conn, cfg, plain)
        finally:
            plain.unlink(missing_ok=True)
    return _read_shard(conn, cfg, path)


def _read_shard(conn: sqlite3.Connection, cfg: Config, path: Path) -> int:
    """Single streaming pass over the tarball; nothing is held in memory."""
    n = 0
    manifest: dict[str, Any] | None = None
    with tarfile.open(path, "r|gz") as tar:
        for m in tar:
            if not m.isfile() or ".." in m.name:
                continue
            f = tar.extractfile(m)
            if f is None:
                continue
            if m.name == "manifest.json":
                manifest = json.loads(f.read())
                for p in manifest["platforms"]:
                    db.upsert(conn, "platform", dict(p), ("id",))
                for s in manifest["sources"]:
                    db.upsert(conn, "source", s, ("id",))
            elif m.name == "pictograms.jsonl.gz":
                if manifest is None:
                    raise ShardError("shard has no manifest before its records")
                with gzip.open(f, "rt", encoding="utf-8") as lines:
                    for line in lines:
                        if line.strip():
                            _import_record(conn, cfg, json.loads(line))
                            n += 1
                            if n % 2000 == 0:
                                conn.commit()
            elif m.name.startswith("norm/") and m.name.endswith(".svg"):
                dest = cfg.norm_path(Path(m.name).stem)
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(f.read())
    if manifest is None:
        raise ShardError(f"{path} is not a handdown shard")
    for e in manifest["errors"]:
        conn.execute(
            "INSERT INTO harvest_error (source_id, item, stage, error, at) VALUES (?,?,?,?,?)",
            (e["source_id"], e["item"], e["stage"], e["error"], e["at"]),
        )
    conn.commit()
    return n


def _import_record(conn: sqlite3.Connection, cfg: Config, rec: dict[str, Any]) -> None:
    raw, feat, ratings = rec.pop("_raw"), rec.pop("_feature"), rec.pop("_ratings")
    if rec.get("sha256"):
        rec["norm_path"] = cfg.rel_norm(rec["sha256"])
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


def encrypt_file(plain: Path, recipients: list[str]) -> Path:
    """age-encrypt ``plain`` to ``plain.age`` and delete the plaintext."""
    import pyrage
    from pyrage import x25519

    try:
        keys = [x25519.Recipient.from_str(r.strip()) for r in recipients]
    except Exception as e:
        raise ShardError(f"invalid age recipient: {e}") from e
    target = plain.with_name(plain.name + ".age")
    pyrage.encrypt_file(str(plain), str(target), keys)
    plain.unlink()
    return target


def decrypt_to(path: Path, identity: Path | None, cfg: Config) -> Path:
    """Decrypt an ``.age`` file into the cache; the caller deletes the result."""
    if identity is None:
        raise ShardError(f"{path} is encrypted: pass the age identity file (--identity or HANDDOWN_AGE_IDENTITY)")
    import pyrage

    plain = cfg.cache / "shards" / path.name.removesuffix(".age")
    plain.parent.mkdir(parents=True, exist_ok=True)
    try:
        pyrage.decrypt_file(str(path), str(plain), [_load_identity(identity)])
    except pyrage.DecryptError as e:
        raise ShardError(f"cannot decrypt {path}: wrong key?") from e
    return plain
