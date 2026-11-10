"""Immutable Parquet partitions; every reader owns an in-memory DuckDB connection."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from pathlib import Path

from .contracts import board_of, canonical, digest, timestamp, utc_now


class Warehouse:
    def __init__(self, repository):
        self.repo = repository

    def publish_history(self, code, bars, receipt):
        """Keep full range series separate from bounded daily research windows."""
        key = digest({"code": code, "bars": bars, "provider": receipt["provider_id"], "available_at": receipt["available_at"]})
        known = receipt["available_at"]
        rows = [{**b, "provenance": {"provider_id": receipt["provider_id"], "source_family": receipt["source_family"],
            "available_at": known, "known_at": known, "ingested_at": known, "adapter_version": "range-1.0.0",
            "event_time": timestamp(b["date"]+"T15:00:00+08:00"), "precision": "observed",
            "evidence_digest": receipt.get("evidence_digest"), "verified_fields": []}} for b in bars]
        return self.publish({"meta": {"trade_date": bars[-1]["date"], "mode": "import", "calendar": [], "available_at": known},
            "stocks": [{"code": code, "name": code, "board_id": board_of(code), "bars": rows}], "sectors": []}, key)

    def publish(self, dataset, snapshot_id):
        import duckdb
        if self.repo.get("partitions", snapshot_id):
            return self.repo.get("partitions", snapshot_id)
        day = dataset["meta"]["trade_date"]
        home = self.repo.home / "parquet" / ("demo" if dataset["meta"].get("mode") == "demo" else "real") / day
        home.mkdir(parents=True, exist_ok=True)
        groups = {"bars": [], "securities": [], "memberships": [], "limit_events": [], "calendar": []}
        for stock in dataset["stocks"]:
            groups["securities"].append({"code": stock["code"], "trade_date": day, "name": stock["name"],
                "board": stock.get("board_id"), "status_history": stock.get("status_history", []),
                "available_at": dataset["meta"].get("available_at")})
            groups["bars"].extend({"code": stock["code"], **bar, "snapshot_id": snapshot_id} for bar in stock.get("bars", []))
            if stock.get("limit"):
                groups["limit_events"].append({"code": stock["code"], "trade_date": day,
                    "available_at": dataset["meta"].get("available_at"), **stock["limit"]})
        for sector in dataset.get("sectors", []):
            groups["memberships"].extend({"sector_id": sector["id"], "code": code, "trade_date": day,
                "classification": sector["classification"], "type": sector["type"],
                "known_at": sector.get("known_at"), "valid_from": sector.get("valid_from"), "valid_to": sector.get("valid_to")}
                for code in sector.get("members", []))
        groups["calendar"] = [{"trade_date": d, "verified": bool(dataset["meta"].get("calendar_verified")),
            "source": dataset["meta"].get("calendar_source"), "known_at": dataset["meta"].get("available_at")} for d in dataset["meta"]["calendar"]]
        partitions = []
        for kind, rows in groups.items():
            if not rows:
                continue
            target = home / (snapshot_id + "-" + kind + ".parquet")
            tmp = target.with_suffix("." + secrets.token_hex(4) + ".tmp")
            with tempfile.TemporaryDirectory(dir=home) as folder:
                source = Path(folder) / "rows.jsonl"
                with source.open("w", encoding="utf-8") as stream:
                    for row in rows:
                        stream.write(canonical(row).decode() + "\n")
                conn = duckdb.connect(":memory:")
                try:
                    conn.execute("SET memory_limit='1GB'")
                    path_sql = str(tmp).replace("'", "''")
                    conn.execute(f"COPY (SELECT * FROM read_json_auto(?,format='newline_delimited',union_by_name=true,maximum_depth=8)) TO '{path_sql}' (FORMAT PARQUET, COMPRESSION ZSTD)", [str(source)])
                finally:
                    conn.close()
            if target.exists():
                tmp.unlink(missing_ok=True)
            else:
                os.replace(tmp, target)
            partitions.append({"kind": kind, "rows": len(rows), "path": str(target.relative_to(self.repo.root)),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        return self.repo.put("partitions", snapshot_id, {"id": snapshot_id, "trade_date": day, "files": partitions}, immutable=True)

    def bars(self, snapshot_id, code=None, start=None, end=None):
        import duckdb
        item = self.repo.get("partitions", snapshot_id)
        if not item:
            raise ValueError("该快照尚未发布Parquet分区")
        entry = next((f for f in item["files"] if f["kind"] == "bars"), None)
        if not entry:
            return []
        path = self.repo.safe_path(entry["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("Parquet文件摘要不一致")
        clauses, args = [], [str(path)]
        for field, op, value in (("code", "=", code), ("date", ">=", start), ("date", "<=", end)):
            if value is not None:
                clauses.append(f'"{field}"{op}?')
                args.append(value)
        conn = duckdb.connect(":memory:")
        try:
            result = conn.execute("SELECT * FROM read_parquet(?)" + (" WHERE " + " AND ".join(clauses) if clauses else "") + ' ORDER BY code,"date"', args)
            names = [r[0] for r in result.description]
            return [dict(zip(names, row)) for row in result.fetchall()]
        finally:
            conn.close()
