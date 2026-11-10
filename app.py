from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
from qsa.version import APP_VERSION
if sys.version_info[:2] == (3, 11) and (ROOT / ".vendor").exists():
    sys.path.insert(0, str(ROOT / ".vendor"))

from qsa.analytics import analyze, stock_detail
from qsa.data import SHANGHAI, SnapshotStore, attach_reference, now_iso, validate_dataset
from qsa.review_service import ReviewService
from qsa.stock_history import StockHistoryService
from qsa.quant_service import QuantService
from qsa.source_service import SourceService


class Workbench:
    def __init__(self, root=ROOT / "data"):
        self.store = SnapshotStore(root)
        self.lock = threading.RLock()
        self.csrf = secrets.token_urlsafe(24)
        self.job = {"state": "idle", "progress": 0, "message": "尚未同步真实行情", "warnings": []}
        self.reviews = ReviewService(self.store, self.review_refresh)
        self.histories = StockHistoryService(self.store, self.lock)
        self.quant = QuantService(self.store, self.lock)
        self.sources = SourceService(self.store)
        self._v2 = None
        from qsa.application import Application
        self.application = Application(self)

    @property
    def v2(self):
        with self.lock:
            if self._v2 is None:
                from qsa.service_v2 import WorkbenchV2
                self._v2 = WorkbenchV2(self.store)
            return self._v2

    def begin_sync(self, message):
        with self.lock:
            if self.job["state"] == "running":
                raise ValueError("已有同步任务正在运行")
            self.job = {"id": secrets.token_hex(8), "state": "running", "progress": 0,
                        "stage": "connecting", "message": message, "warnings": [],
                        "started_at": now_iso(), "updated_at": now_iso(), "ended_at": None,
                        "detail": {}}

    def save_sync(self, dataset, message=None):
        self.progress(97, "检查日期、价格、单位、重复记录与未来数据", {"stage": "validate"})
        validate_dataset(dataset)
        self.progress(99, "校验通过，正在保存行情快照", {"stage": "save"})
        with self.lock:
            filename = self.store.save(dataset)
            self.job.update(state="done", progress=100, stage="complete", ended_at=now_iso(),
                            updated_at=now_iso(), snapshot=filename,
                            message=message or f"同步完成：{len(dataset['stocks'])}只证券，快照已保存",
                            warnings=dataset["meta"].get("notes", []),
                            detail={"history_total": dataset["meta"].get("history_target_count"),
                                    "history_success": dataset["meta"].get("history_success_count")})

    def fail_sync(self, error):
        with self.lock:
            self.job.update(state="error", ended_at=now_iso(), updated_at=now_iso(),
                            message=f"同步未完成：{type(error).__name__}: {str(error)[:500]}。已保留原快照。")

    def review_refresh(self, day):
        from qsa.free_provider import fetch_free
        self.begin_sync("抖音复盘正在更新行情")
        try:
            dataset = fetch_free({"date": day, "industry_limit": 12, "theme_limit": 8}, self.store.root, self.progress, detailed_progress=self.progress)
            self.save_sync(dataset, "复盘所需行情已更新")
        except Exception as error:
            self.fail_sync(error)
            raise

    def progress(self, percent, message, detail=None):
        with self.lock:
            if self.job["state"] != "running":
                return
            detail = dict(detail or {})
            stage = detail.pop("stage", None)
            # 100% is reserved for a validated snapshot that has actually been saved.
            self.job.update(progress=max(0, min(99, int(percent))), message=message,
                            updated_at=now_iso(), detail=detail)
            if stage:
                self.job["stage"] = stage

    def sync(self, payload):
        self.begin_sync("正在连接免费数据源")

        def worker():
            try:
                from qsa.free_provider import fetch_free
                dataset = fetch_free(payload, self.store.root, self.progress, detailed_progress=self.progress)
                self.save_sync(dataset)
            except Exception as error:
                # Never switch to demo or overwrite the previous valid snapshot on failure.
                self.fail_sync(error)

        self.sync_worker = threading.Thread(target=worker, daemon=True)
        self.sync_worker.start()
        with self.lock:
            return dict(self.job)


def make_handler(workbench):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, status, payload, mime="application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def guard(self, mutate=False):
            host = self.headers.get("Host", "").split(":")[0]
            if host not in ("localhost", "127.0.0.1"):
                self.send(403, {"error": "仅允许本机访问"})
                return False
            if mutate and self.headers.get("X-Workbench-Token") != workbench.csrf:
                self.send(403, {"error": "请求校验失败，请刷新页面"})
                return False
            return True

        def do_GET(self):
            if not self.guard():
                return
            result = workbench.application.request("GET", self.path)
            if result.mime == "application/json; charset=utf-8":
                self.send(result.status, result.payload)
            else:
                self.send(result.status, result.payload, result.mime)

        def do_POST(self):
            if not self.guard(mutate=True):
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 80 * 1024 * 1024:
                    raise ValueError("文件为空或超过80MB")
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("请求内容必须为对象")
                result = workbench.application.request("POST", self.path, payload)
                if result.mime == "application/json; charset=utf-8":
                    self.send(result.status, result.payload)
                else:
                    self.send(result.status, result.payload, result.mime)
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                self.send(400, {"error": str(error)[:400]})

    return Handler


def main():
    parser = argparse.ArgumentParser(description="涨势 · A股盘后研究台")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    workbench = Workbench(args.data_dir or ROOT / "data")
    workbench.v2.startup_check()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(workbench))
    workbench.reviews.start_scheduler()
    url = f"http://127.0.0.1:{args.port}"
    print(f"Workbench ready: {url}", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        workbench.histories.close()
        workbench.reviews.close()
        server.server_close()


if __name__ == "__main__":
    main()
