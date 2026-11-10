"""Durable notification inbox with expiry and idempotent deliveries."""
from __future__ import annotations

from .contracts import digest, timestamp, utc_now


class Alerts:
    def __init__(self, repository):
        self.repo = repository

    def emit(self, event, title, body, severity="C", valid_until=None, signal_id=None):
        if severity not in ("A", "B", "C"):
            raise ValueError("提醒级别无效")
        key = digest({"event": event, "signal_id": signal_id})[:32]
        old = self.repo.get("alerts", key)
        if old:
            return old
        row = {"id": key, "event": event, "title": str(title)[:200], "body": str(body)[:4000], "severity": severity,
            "valid_until": timestamp(valid_until) if valid_until else None, "signal_id": signal_id,
            "created_at": utc_now(), "read": False, "delivered_at": None}
        return self.repo.put("alerts", key, row, immutable=True)

    def list(self, at=None):
        now = timestamp(at or utc_now())
        return [{**r, "expired": bool(r.get("valid_until") and r["valid_until"] < now)} for r in self.repo.list("alerts")]

    def acknowledge(self, key):
        row = self.repo.get("alerts", key)
        if not row:
            raise ValueError("提醒不存在")
        return self.repo.put("alerts", key, {**row, "read": True}, expected=row["_revision"])

    def deliver(self):
        """Windows local tray balloons; inbox remains authoritative on headless hosts."""
        from datetime import datetime
        from .data import SHANGHAI
        today = datetime.now(SHANGHAI).date().isoformat()
        already = sum(bool(r.get("delivered_at")) and datetime.fromisoformat(r["delivered_at"]).astimezone(SHANGHAI).date().isoformat() == today for r in self.repo.list("alerts"))
        pending = [r for r in self.list() if not r["expired"] and not r.get("delivered_at")]
        if not pending:
            return {"delivered": 0}
        import os
        if os.name != "nt":
            return {"delivered": 0, "reason": "非Windows环境，提醒保留消息中心"}
        from .windows_notify import balloon
        count = 0
        for row in pending[:max(0, 10-already)]:
            if balloon(row["title"], row["body"]):
                self.repo.put("alerts", row["id"], {**row, "delivered_at": utc_now()}, expected=row["_revision"])
                count += 1
        return {"delivered": count, "inbox_pending": len(pending)-count}
