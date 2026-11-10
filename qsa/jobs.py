"""Durable jobs with leases, cancellation and transactional idempotency."""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .contracts import canonical, digest, timestamp, utc_now
from .repository import Conflict

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class JobStore:
    def __init__(self, repository):
        self.repo = repository

    @staticmethod
    def decode(row):
        if not row:
            return None
        result = dict(row)
        for field in ("payload", "checkpoint", "result"):
            if result.get(field):
                result[field] = json.loads(result[field])
        result["cancel_requested"] = bool(result["cancel_requested"])
        return result

    def get(self, key):
        with self.repo.connect() as db:
            result = self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (key,)).fetchone())
        if not result:
            raise ValueError("任务不存在")
        return result

    def list(self, limit=100):
        with self.repo.connect() as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM jobs ORDER BY created_at DESC,id LIMIT ?", (min(int(limit), 1000),))]

    def enqueue(self, kind, day, payload=None, input_version=""):
        value = payload or {}
        key = digest({"kind": kind, "date": day, "input": input_version, "payload": value})
        now, job_id = utc_now(), secrets.token_hex(12)
        with self.repo.connect(write=True) as db:
            db.execute("""INSERT OR IGNORE INTO jobs(id,dedupe_key,kind,trade_date,state,payload,created_at,updated_at)
             VALUES(?,?,?,?,'queued',?,?,?)""", (job_id, key, kind, day, canonical(value).decode(), now, now))
            row = db.execute("SELECT * FROM jobs WHERE dedupe_key=?", (key,)).fetchone()
        return self.decode(row)

    def claim(self, key):
        owner, now = secrets.token_hex(12), utc_now()
        with self.repo.connect(write=True) as db:
            count = db.execute("""UPDATE jobs SET state='running',owner=?,heartbeat=?,updated_at=?,
             attempt=attempt+1,error=NULL WHERE id=? AND state='queued' AND cancel_requested=0""", (owner, now, now, key)).rowcount
        return owner if count else None

    def progress(self, key, owner, percent, message, checkpoint=None):
        now = utc_now()
        with self.repo.connect(write=True) as db:
            count = db.execute("""UPDATE jobs SET progress=?,message=?,checkpoint=COALESCE(?,checkpoint),heartbeat=?,updated_at=?
             WHERE id=? AND state='running' AND owner=?""", (
                max(0, min(99, int(percent))), str(message)[:1000], canonical(checkpoint).decode() if checkpoint is not None else None,
                now, now, key, owner)).rowcount
        if not count:
            raise Conflict("任务租约已失效")
        if self.get(key)["cancel_requested"]:
            raise InterruptedError("任务已取消")

    def heartbeat(self, key, owner):
        with self.repo.connect(write=True) as db:
            return db.execute("UPDATE jobs SET heartbeat=?,updated_at=? WHERE id=? AND owner=? AND state='running'",
                (utc_now(), utc_now(), key, owner)).rowcount

    def finish(self, key, owner, state, result=None, error=None):
        if state not in TERMINAL:
            raise ValueError("无效任务结束状态")
        with self.repo.connect(write=True) as db:
            count = db.execute("""UPDATE jobs SET state=?,progress=CASE WHEN ?='completed' THEN 100 ELSE progress END,
             result=?,error=?,updated_at=?,heartbeat=NULL,owner=NULL WHERE id=? AND owner=? AND state='running'""", (
                state, state, canonical(result).decode() if result is not None else None,
                str(error)[:2000] if error else None, utc_now(), key, owner)).rowcount
        if not count:
            raise Conflict("任务结束时租约已失效")
        return self.get(key)

    def cancel(self, key):
        with self.repo.connect(write=True) as db:
            db.execute("""UPDATE jobs SET cancel_requested=1,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,
             updated_at=? WHERE id=? AND state IN ('queued','running')""", (utc_now(), key))
        return self.get(key)

    def recover(self, stale_seconds=120):
        before = (datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)).isoformat(timespec="microseconds")
        with self.repo.connect(write=True) as db:
            rows = db.execute("SELECT id FROM jobs WHERE state='running' AND (heartbeat IS NULL OR heartbeat<?)", (before,)).fetchall()
            db.execute("""UPDATE jobs SET state='interrupted',error='任务进程失去心跳，可从检查点重试',owner=NULL,
             updated_at=? WHERE state='running' AND (heartbeat IS NULL OR heartbeat<?)""", (utc_now(), before))
        return [r["id"] for r in rows]

    def retry(self, key):
        with self.repo.connect(write=True) as db:
            count = db.execute("""UPDATE jobs SET state='queued',owner=NULL,cancel_requested=0,error=NULL,
             updated_at=? WHERE id=? AND state IN ('failed','cancelled','interrupted')""", (utc_now(), key)).rowcount
        if not count:
            raise Conflict("只能重试失败、取消或中断的任务")
        return self.get(key)


def worker_command(root, job_id):
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).parent / "ZhangshiWorker.exe"), "--workbench-worker", "--data-dir", str(root), "execute", "--job-id", job_id]
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "workbench.py"), "--data-dir", str(root), "execute", "--job-id", job_id]


def launch_worker(root, job_id):
    folder = Path(root) / "v2" / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    temp = Path(root) / "v2" / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "TEMP": str(temp), "TMP": str(temp)}
    with (folder / (job_id + ".log")).open("ab") as log:
        child = subprocess.Popen(worker_command(root, job_id), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env, close_fds=True)
    return child.pid
