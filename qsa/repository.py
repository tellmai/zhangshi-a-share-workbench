"""Transactional metadata and immutable, hash-verified artifacts for V2."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .contracts import canonical, digest, timestamp, utc_now

DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(
 collection TEXT NOT NULL, key TEXT NOT NULL, revision INTEGER NOT NULL,
 created_at TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(collection,key,revision));
CREATE INDEX IF NOT EXISTS document_latest ON documents(collection,key,revision DESC);
CREATE TABLE IF NOT EXISTS artifacts(
 digest TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, size INTEGER NOT NULL,
 kind TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(
 digest TEXT PRIMARY KEY REFERENCES artifacts(digest), trade_date TEXT NOT NULL,
 mode TEXT NOT NULL, available_at TEXT, observed_at TEXT NOT NULL,
 origin TEXT NOT NULL, quality TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS snapshot_asof ON snapshots(mode,trade_date,available_at,observed_at);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
 trade_date TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL,
 progress INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
 checkpoint TEXT NOT NULL DEFAULT '{}', result TEXT, error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, heartbeat TEXT,
 owner TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0, attempt INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS job_state ON jobs(state,updated_at);
CREATE TABLE IF NOT EXISTS migrations(
 origin TEXT NOT NULL, digest TEXT NOT NULL, kind TEXT NOT NULL,
 migrated_at TEXT NOT NULL, PRIMARY KEY(origin,digest));
CREATE TABLE IF NOT EXISTS audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, action TEXT NOT NULL,
 entity TEXT NOT NULL, payload TEXT NOT NULL);
"""


class Conflict(ValueError):
    pass


class Repository:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.home = self.root / "v2"
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "workbench.sqlite3"
        with self.connect() as db:
            # Older Python remains readable with DELETE journaling; release uses 3.12.
            version = sqlite3.sqlite_version_info
            patched = version >= (3, 51, 3) or (3, 50, 7) <= version < (3, 51, 0) or (3, 44, 6) <= version < (3, 45, 0)
            db.execute("PRAGMA journal_mode=" + ("WAL" if patched else "DELETE"))
            db.executescript(DDL)
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES(2,?)", (utc_now(),))

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def audit(self, action, entity, value=None, db=None):
        args = (utc_now(), action, entity, canonical(value or {}).decode())
        if db is not None:
            db.execute("INSERT INTO audit(at,action,entity,payload) VALUES(?,?,?,?)", args)
        else:
            with self.connect(write=True) as conn:
                conn.execute("INSERT INTO audit(at,action,entity,payload) VALUES(?,?,?,?)", args)

    def get(self, collection, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT revision,payload,created_at FROM documents WHERE collection=? AND key=? ORDER BY revision DESC LIMIT 1", (collection, key)).fetchone()
        if not row:
            return default
        value = json.loads(row["payload"])
        return {**value, "_revision": row["revision"], "_saved_at": row["created_at"]}

    def list(self, collection, limit=1000):
        with self.connect() as db:
            rows = db.execute("""SELECT d.* FROM documents d JOIN
             (SELECT key,MAX(revision) AS rev FROM documents WHERE collection=? GROUP BY key) x
             ON d.key=x.key AND d.revision=x.rev WHERE d.collection=?
             ORDER BY d.created_at DESC,d.key LIMIT ?""", (collection, collection, min(int(limit), 100000))).fetchall()
        return [{**json.loads(r["payload"]), "_revision": r["revision"], "_saved_at": r["created_at"]} for r in rows]

    def revision_token(self, collections):
        """A cross-process invalidation token for immutable revision collections."""
        placeholders = ",".join("?" for _ in collections)
        with self.connect() as db:
            return db.execute("SELECT COALESCE(MAX(rowid),0) FROM documents WHERE collection IN ("+placeholders+")", tuple(collections)).fetchone()[0]

    def put(self, collection, key, value, expected=None, immutable=False):
        value = {k: v for k, v in value.items() if not k.startswith("_")}
        raw = canonical(value).decode()
        with self.connect(write=True) as db:
            old = db.execute("SELECT revision,payload FROM documents WHERE collection=? AND key=? ORDER BY revision DESC LIMIT 1", (collection, key)).fetchone()
            if immutable and old:
                if old["payload"] != raw:
                    raise Conflict("已冻结记录不能覆盖")
                return self.get(collection, key)
            if expected is not None and ((expected == 0 and old is not None) or (expected != 0 and (not old or old["revision"] != expected))):
                raise Conflict("记录已被其他操作修改，请重新读取")
            revision = old["revision"] + 1 if old else 1
            db.execute("INSERT INTO documents VALUES(?,?,?,?,?)", (collection, key, revision, utc_now(), raw))
            self.audit("append_revision", f"{collection}/{key}", {"revision": revision}, db)
        return self.get(collection, key)

    def revisions(self, collection, key):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM documents WHERE collection=? AND key=? ORDER BY revision", (collection, key)).fetchall()
        return [{**json.loads(r["payload"]), "_revision": r["revision"], "_saved_at": r["created_at"]} for r in rows]

    def safe_path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("文件路径越界")
        return path

    def object(self, value, kind="json"):
        raw = canonical(value)
        key = hashlib.sha256(raw).hexdigest()
        path = self.home / "objects" / key[:2] / (key + ".json.gz")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if gzip.decompress(path.read_bytes()) != raw:
                raise ValueError("冻结文件摘要冲突")
        else:
            tmp = path.with_suffix("." + secrets.token_hex(6) + ".tmp")
            try:
                with tmp.open("xb") as out:
                    out.write(gzip.compress(raw, mtime=0))
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
        with self.connect(write=True) as db:
            db.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?,?)", (key, str(path.relative_to(self.root)), len(raw), kind, utc_now()))
        return key

    def read_object(self, key):
        if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("冻结输入摘要无效")
        with self.connect() as db:
            row = db.execute("SELECT path FROM artifacts WHERE digest=?", (key,)).fetchone()
        if not row:
            raise ValueError("冻结输入不存在")
        raw = gzip.decompress(self.safe_path(row["path"]).read_bytes())
        if hashlib.sha256(raw).hexdigest() != key:
            raise ValueError("冻结输入校验失败")
        return json.loads(raw)

    def register_snapshot(self, dataset, origin="api", observed=False):
        from .quality import assess
        key = self.object(dataset, "snapshot")
        meta = dataset["meta"]
        available = meta.get("available_at")
        if available:
            available = timestamp(available)
        elif observed:
            available = utc_now()
        observation = timestamp(meta.get("fetched_at") or utc_now())
        quality = assess(dataset)
        with self.connect(write=True) as db:
            db.execute("INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?,?,?,?)", (
                key, meta["trade_date"], "demo" if meta.get("mode") == "demo" else "real",
                available, observation, str(origin), canonical(quality).decode(), utc_now()))
        return {"snapshot_id": key, "quality": quality, "available_at": available}

    def snapshot_rows(self, mode="real", until=None):
        sql = "SELECT * FROM snapshots WHERE mode=?"
        args = [mode]
        if until:
            sql += " AND trade_date<=?"
            args.append(until)
        with self.connect() as db:
            rows = db.execute(sql + " ORDER BY trade_date,COALESCE(available_at,observed_at),created_at,digest", args).fetchall()
        return [{**dict(r), "quality": json.loads(r["quality"])} for r in rows]

    def asof_snapshot(self, trade_date, decision_time, mode="real", strict=True):
        cutoff = timestamp(decision_time)
        with self.connect() as db:
            row = db.execute("""SELECT digest FROM snapshots WHERE mode=? AND trade_date=?
             AND available_at IS NOT NULL AND available_at<=?
             ORDER BY available_at DESC,created_at DESC,digest DESC LIMIT 1""", (mode, trade_date, cutoff)).fetchone()
        if not row:
            if strict:
                raise ValueError(f"{trade_date} 没有在决策时点前可见的快照")
            return None
        return self.read_object(row["digest"])

    def audit_events(self, limit=100):
        with self.connect() as db:
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (min(int(limit), 1000),))]
