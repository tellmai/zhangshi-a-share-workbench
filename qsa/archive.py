"""Legacy migration, code manifests and verifiable portable backups."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from .contracts import canonical, digest, utc_now
from .quality import normalize
from .version import APP_VERSION, ENGINE_VERSION, FACTOR_VERSION, RULE_VERSION


def code_manifest():
    root = Path(__file__).resolve().parents[1]
    if getattr(sys, "frozen", False) and (root / "build-info.json").exists():
        return json.loads((root / "build-info.json").read_text(encoding="utf-8"))
    paths = sorted([p for folder in ("qsa", "scripts") for p in (root / folder).glob("*.py")] + [root / "app.py", root / "desktop.py"])
    files = {str(p.relative_to(root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    commit = None
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
            timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"code_hash": digest(files), "git_commit": commit, "files": files}


def run_manifest(snapshot_id, config, decision_time, **extra):
    return {"app_version": APP_VERSION, "engine_version": ENGINE_VERSION, "factor_version": FACTOR_VERSION,
        "rule_version": RULE_VERSION, "snapshot_id": snapshot_id, "config_hash": digest(config),
        "decision_time": decision_time, "code": code_manifest(), **extra}


def migrate(repository, progress=lambda *_: None, cancelled=lambda: False, parquet=False):
    root = repository.root
    paths = sorted((root / "snapshots").glob("*.json"))
    if (root / "active.json").exists():
        paths.append(root / "active.json")
    for folder, pattern in (("quant", "**/*.json"), ("reviews", "**/*.json"), ("review", "**/*.json"), ("sources", "*.json")):
        paths.extend(sorted((root / folder).glob(pattern)))
    imported, skipped, failures = 0, 0, []
    for i, path in enumerate(paths):
        if cancelled():
            raise InterruptedError("迁移已取消")
        origin = str(path.relative_to(root)).replace("\\", "/")
        progress(int(i / max(len(paths), 1) * 95), f"核对旧资料 {i+1}/{len(paths)} · {origin}")
        try:
            raw = path.read_bytes()
            raw_digest = hashlib.sha256(raw).hexdigest()
            with repository.connect() as db:
                exists = db.execute("SELECT 1 FROM migrations WHERE origin=? AND digest=?", (origin, raw_digest)).fetchone()
            if exists:
                skipped += 1
                continue
            value = json.loads(raw.decode("utf-8-sig"))
            if isinstance(value, dict) and value.get("schema_version") in (1, 2) and "stocks" in value:
                normalized = normalize(value, observed=False)
                result = repository.register_snapshot(normalized, origin=origin)
                if parquet:
                    from .warehouse import Warehouse
                    Warehouse(repository).publish(normalized, result["snapshot_id"])
                kind = "snapshot"
            else:
                kind = "legacy_record"
                object_id = repository.object(value, kind)
                repository.put("legacy", digest({"origin": origin, "digest": raw_digest}),
                    {"id": raw_digest, "origin": origin, "object_id": object_id}, immutable=True)
            with repository.connect(write=True) as db:
                db.execute("INSERT OR IGNORE INTO migrations VALUES(?,?,?,?)", (origin, raw_digest, kind, utc_now()))
            imported += 1
        except (ValueError, TypeError, KeyError, OSError) as error:
            failures.append({"origin": origin, "error": str(error)[:400]})
    return {"imported": imported, "skipped": skipped, "failures": failures, "original_files_modified": False}


def backup(repository, destination=None, progress=lambda *_: None):
    from .file_lock import data_lock
    with data_lock(repository.root):
        return _backup(repository, destination, progress)


def _backup(repository, destination=None, progress=lambda *_: None):
    root = repository.root
    folder = repository.home / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    name = utc_now().replace(":", "").replace("+", "_") + "-" + secrets.token_hex(3) + ".zip"
    target = Path(destination).resolve() if destination else folder / name
    if target.exists():
        raise ValueError("备份目标已存在")
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"format": 2, "created_at": utc_now(), "app_version": APP_VERSION, "files": {}}
    tmp = target.with_suffix(".incomplete")
    with tempfile.TemporaryDirectory(dir=repository.home) as scratch:
        db_path = Path(scratch) / "workbench.sqlite3"
        with repository.connect() as source:
            dest = sqlite3.connect(db_path)
            try:
                source.backup(dest)
            finally:
                dest.close()
        files = []
        for path in root.rglob("*"):
            if not path.is_file() or path.is_symlink() or path.resolve().is_relative_to(Path(scratch).resolve()):
                continue
            relative = path.relative_to(root)
            if path.resolve() in (target, tmp) or any(part in ("backups", "logs", "tmp", "desktop-profile", "restore") for part in relative.parts):
                continue
            if path == repository.path or path.name.endswith(("-wal", "-shm", ".tmp", ".log", ".lock")):
                continue
            files.append((path, str(relative).replace("\\", "/")))
        files.append((db_path, "v2/workbench.sqlite3"))
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
                for i, (path, relative) in enumerate(sorted(files, key=lambda item: item[1])):
                    raw = path.read_bytes()
                    manifest["files"][relative] = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
                    archive.writestr(relative, raw)
                    progress(int(i / max(len(files), 1) * 95), f"备份 {i+1}/{len(files)}")
                archive.writestr("BACKUP-MANIFEST.json", canonical(manifest))
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
    verify_backup(target)
    return {"id": target.name, "path": str(target), "files": len(manifest["files"]), "verified": True,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def verify_backup(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("备份包含重复路径")
        manifest = json.loads(archive.read("BACKUP-MANIFEST.json"))
        if manifest.get("format") != 2 or set(names) != set(manifest["files"]) | {"BACKUP-MANIFEST.json"}:
            raise ValueError("备份清单不完整")
        for name, expected in manifest["files"].items():
            if name.startswith(("/", "\\")) or ":" in name or ".." in Path(name.replace("\\", "/")).parts:
                raise ValueError("备份包含越界路径")
            raw = archive.read(name)
            if len(raw) != expected["size"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
                raise ValueError("备份文件校验失败：" + name)
    return manifest


def restore(path, destination):
    manifest = verify_backup(path)
    target = Path(destination).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("恢复目标必须是空目录，禁止覆盖运行中的数据")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for name in manifest["files"]:
            out = (target / name).resolve()
            if not out.is_relative_to(target):
                raise ValueError("恢复路径越界")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(archive.read(name))
    db = sqlite3.connect(target / "v2" / "workbench.sqlite3")
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("恢复数据库完整性校验失败")
    finally:
        db.close()
    from .repository import Repository
    restored = Repository(target)
    with restored.connect() as db:
        artifacts = [r[0] for r in db.execute("SELECT digest FROM artifacts")]
    for artifact in artifacts:
        restored.read_object(artifact)
    return {"path": str(target), "files": len(manifest["files"]), "verified": True}


def rotate_backups(repository):
    """Keep the newest seven daily and four earlier weekly restore points."""
    folder = repository.home / "backups"
    paths = sorted(folder.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    keep, days, weeks = set(), set(), set()
    from datetime import datetime
    stamps = {p: datetime.fromtimestamp(p.stat().st_mtime) for p in paths}
    for path in paths:
        stamp = stamps[path]
        day = stamp.date().isoformat()
        if day not in days and len(days) < 7:
            keep.add(path); days.add(day)
    daily_weeks = {stamps[p].isocalendar()[:2] for p in keep}
    for path in paths:
        week = stamps[path].isocalendar()[:2]
        if week not in daily_weeks and week not in weeks and len(weeks) < 4:
            keep.add(path); weeks.add(week)
    removed = []
    for path in paths:
        if path not in keep:
            if not path.resolve().is_relative_to(folder.resolve()):
                raise ValueError("备份轮换路径异常")
            path.unlink()
            removed.append(path.name)
    return {"kept": len(keep), "removed": removed}
