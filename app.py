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
APP_VERSION = "0.6.0"
if (ROOT / ".vendor").exists():
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
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            try:
                if parsed.path == "/api/status":
                    with workbench.lock:
                        self.send(200, {"csrf": workbench.csrf, "job": workbench.job, "active_meta": workbench.store.active["meta"], "today": datetime.now(SHANGHAI).date().isoformat(), "version": APP_VERSION, "application_id": "after-close-lab", "runtime":"web"})
                elif parsed.path == "/api/analysis":
                    dataset = workbench.store.read(params.get("mode", ["active"])[0])
                    self.send(200, analyze(dataset))
                elif parsed.path == "/api/stock":
                    dataset = workbench.store.read(params.get("mode", ["active"])[0])
                    self.send(200, stock_detail(dataset, params.get("code", [""])[0]))
                elif parsed.path == "/api/stock/history":
                    self.send(200, workbench.histories.status(params.get("code", [""])[0], params.get("mode", ["active"])[0], params.get("date", [None])[0]))
                elif parsed.path == "/api/snapshot":
                    self.send(200, workbench.store.read(params.get("mode", ["active"])[0]))
                elif parsed.path == "/api/reviews":
                    self.send(200, workbench.reviews.status())
                elif parsed.path == "/api/sources":
                    self.send(200, workbench.sources.status())
                elif parsed.path == "/api/quant/status":
                    self.send(200, workbench.quant.status(params.get("mode", ["active"])[0]))
                elif parsed.path == "/api/quant/stocks":
                    self.send(200, workbench.quant.stock_choices(params.get("mode", ["active"])[0], params.get("q", [""])[0]))
                elif parsed.path == "/api/quant/result":
                    self.send(200, workbench.quant.store.read_run(params.get("id", [""])[0]))
                elif parsed.path == "/api/quant/export":
                    body, mime = workbench.quant.store.export(params.get("id", [""])[0], params.get("format", ["json"])[0])
                    self.send(200, body, mime)
                elif parsed.path == "/api/reviews/report":
                    self.send(200, workbench.reviews.store.read(params.get("id", [""])[0]))
                elif parsed.path == "/api/reviews/artifact":
                    name = params.get("name", [""])[0]
                    body = workbench.reviews.store.artifact(params.get("id", [""])[0], name)
                    mime = {"svg": "image/svg+xml", "png": "image/png", "zip": "application/zip", "json": "application/json; charset=utf-8", "txt": "text/plain; charset=utf-8"}.get(name.rsplit(".", 1)[-1], "application/octet-stream")
                    self.send(200, body, mime)
                elif parsed.path == "/assets/review-room-v1.png":
                    self.send(200, (ROOT / "static" / "assets" / "review-room-v1.png").read_bytes(), "image/png")
                elif parsed.path in ("/", "/index.html", "/app.js", "/review.js", "/stock.js", "/quant.js", "/sources.js", "/shortline.js", "/style.css", "/review.css", "/quant.css", "/sources.css", "/shortline.css"):
                    filename = "index.html" if parsed.path == "/" else parsed.path[1:]
                    mime = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8"}[filename.rsplit(".", 1)[1]]
                    self.send(200, (ROOT / "static" / filename).read_bytes(), mime)
                else:
                    self.send(404, {"error": "页面不存在"})
            except (ValueError, TypeError, KeyError, OSError) as error:
                self.send(400, {"error": str(error)[:400]})

        def do_POST(self):
            if not self.guard(mutate=True):
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 80 * 1024 * 1024:
                    raise ValueError("文件为空或超过80MB")
                payload = json.loads(self.rfile.read(size))
                path = urlparse(self.path).path
                if path == "/api/analyze":
                    dataset = workbench.store.read(payload.get("mode", "active"))
                    self.send(200, analyze(dataset, payload.get("config")))
                elif path == "/api/sync":
                    job = workbench.sync(payload)
                    self.send(202, {"message": "同步已启动", "job": job})
                elif path == "/api/stock/history":
                    self.send(202, workbench.histories.start(payload))
                elif path == "/api/import":
                    dataset = payload.get("dataset", {})
                    validate_dataset(dataset)
                    if dataset["meta"].get("mode") != "demo":
                        dataset["meta"]["mode"] = "import"
                    with workbench.lock:
                        workbench.store.save(dataset)
                    self.send(200, {"message": "快照已通过结构与价格检查并保存"})
                elif path == "/api/reference":
                    dataset = workbench.store.read(payload.get("mode", "active"))
                    if dataset["meta"].get("mode") == "demo":
                        raise ValueError("演示数据不能进行真实行情核对，请先同步或导入真实快照")
                    dataset, count = attach_reference(dataset, payload)
                    with workbench.lock:
                        workbench.store.save(dataset)
                    self.send(200, {"message": f"已核对{count}只证券的同日收盘价"})
                elif path == "/api/reviews/generate":
                    self.send(202, workbench.reviews.start(payload))
                elif path == "/api/reviews/settings":
                    self.send(200, workbench.reviews.store.update_settings(payload))
                elif path == "/api/reviews/title":
                    self.send(200, workbench.reviews.store.choose_title(payload.get('id'), payload.get('index')))
                elif path == "/api/sources/settings":
                    self.send(200, workbench.sources.settings(payload))
                elif path == "/api/sources/test":
                    self.send(202, workbench.sources.start(payload))
                elif path == "/api/quant/strategy":
                    self.send(200, workbench.quant.store.save_strategy(payload))
                elif path == "/api/quant/stocks/resolve":
                    self.send(200, workbench.quant.stock_choices(payload.get("mode","active"), values=payload.get("codes",[])))
                elif path == "/api/quant/stocks/check":
                    self.send(200, workbench.quant.check_stocks(payload))
                elif path == "/api/quant/run":
                    self.send(202, workbench.quant.start(payload))
                elif path == "/api/quant/replay":
                    if not payload.get("id"):
                        raise ValueError("请选择需要复现的运行记录")
                    self.send(202, workbench.quant.start({}, replay_id=payload.get("id")))
                elif path == "/api/quant/cancel":
                    self.send(200, workbench.quant.cancel_run())
                else:
                    self.send(404, {"error": "接口不存在"})
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                self.send(400, {"error": str(error)[:400]})
            except Exception:
                self.send(500, {"error": "内部处理失败，原始快照未主动替换；请检查文件格式或重启服务"})

    return Handler


def main():
    parser = argparse.ArgumentParser(description="涨势 · A股盘后研究台")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    workbench = Workbench()
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
