"""Background generation and opt-in in-process scheduling (Asia/Shanghai)."""
from __future__ import annotations

import json
import threading
from datetime import datetime

from .data import SHANGHAI, atomic_json, iso_date, now_iso
from .review import ReviewStore, build_review, prior_session, trading_day
from .review_sources import collect_context


class ReviewService:
    def __init__(self, snapshots, refresh=None):
        self.snapshots = snapshots
        self.store = ReviewStore(snapshots.root)
        self.refresh = refresh
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.job = {"state": "idle", "message": "尚未生成抖音复盘", "report_id": None}
        self.scheduler = None

    def status(self):
        with self.lock:
            return {"settings": self.store.settings(), "job": dict(self.job), "history": self.store.history(),
                "scheduler_running": self.scheduler is not None and self.scheduler.is_alive(),
                "schedule_note": "本机服务运行且电脑唤醒时执行；错过当天时间会在当天启动后补跑一次。休市静默跳过，跨年日历缺失则暂停生成。",
                "migration": {"task_title": "A股抖音复盘收盘", "source": "同名会话的最新任务描述与免责声明", "original_schedule_verified": False}}

    def progress(self, message):
        with self.lock:
            self.job["message"] = message

    def previous_snapshot(self, day):
        previous = prior_session(day)
        if not previous:
            return None
        folder = self.snapshots.root / "snapshots"
        for path in sorted(folder.glob(previous + "-*.json"), reverse=True):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if value["meta"]["trade_date"] == previous and value["meta"].get("mode") != "demo":
                    return value
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    def generate(self, day, refresh=False, offline=False, extra_urls=None):
        day = iso_date(day)
        settings = self.store.settings()
        current = datetime.now(SHANGHAI)
        if day > current.date().isoformat() or (day == current.date().isoformat() and current.hour < 15):
            raise ValueError("目标交易日尚未收盘")
        is_open = trading_day(day, settings["closed_dates"])
        if is_open is False:
            return {"status": "skipped", "failure": "非交易日：静默跳过", "sources": []}
        if is_open is None:
            raise ValueError("目标年份的交易日历尚未核验")
        if refresh:
            if self.refresh is None:
                raise ValueError("当前运行方式未配置行情更新函数")
            self.progress("更新目标交易日行情，原有快照会保留")
            self.refresh(day)
        dataset = self.snapshots.read()
        evidence = self.snapshots.root / "review-evidence" / (day + "-" + datetime.now(SHANGHAI).strftime("%H%M%S%f"))
        if offline:
            candidates = sorted((self.snapshots.root / "review-evidence").glob(day + "-*/context.json"), reverse=True)
            if not candidates:
                raise ValueError("没有该日期的已读取来源缓存，请先联网生成一次")
            context = json.loads(candidates[0].read_text(encoding="utf-8"))
        else:
            context = collect_context(day, evidence, settings["extra_urls"] if extra_urls is None else extra_urls, progress=self.progress)
        self.progress("核对日期、筛选三种风格代表股并检查文稿")
        report = build_review(dataset, context, self.previous_snapshot(day), self.store.history(), settings["closed_dates"])
        report["evidence_directory"] = str(evidence.relative_to(self.snapshots.root)) if not offline else str(candidates[0].parent.relative_to(self.snapshots.root))
        # No archive or exported notice on non-trading days.
        if report["status"] == "skipped":
            return report
        return self.store.save(report)

    def start(self, payload):
        day = iso_date(payload.get("date") or datetime.now(SHANGHAI).date().isoformat())
        current = datetime.now(SHANGHAI)
        if day > current.date().isoformat() or (day == current.date().isoformat() and current.hour < 15):
            raise ValueError("目标交易日尚未收盘")
        is_open = trading_day(day, self.store.settings()["closed_dates"])
        if is_open is False:
            return {"skipped": True, "message": "非交易日，不生成复盘"}
        if is_open is None:
            raise ValueError("目标年份的交易日历尚未核验，暂停生成")
        for flag in ("refresh", "offline"):
            if flag in payload and type(payload[flag]) is not bool:
                raise ValueError("运行选项格式无效")
        with self.lock:
            if self.job["state"] == "running":
                raise ValueError("已有抖音复盘正在生成，请等待完成")
            self.job = {"state": "running", "message": "正在准备来源核验", "date": day, "started_at": now_iso(), "report_id": None}

        def worker():
            try:
                report = self.generate(day, payload.get("refresh", False), payload.get("offline", False))
                with self.lock:
                    self.job.update(state="done", report_id=report.get("id"), finished_at=now_iso(),
                        message="已生成待复核草稿与来源记录" if report["status"] == "draft" else report.get("failure", "休市跳过"))
            except Exception as error:
                with self.lock:
                    self.job.update(state="error", message=f"生成未完成：{type(error).__name__}: {str(error)[:300]}。原有复盘保留。", finished_at=now_iso())
        threading.Thread(target=worker, name="douyin-review", daemon=True).start()
        return {"message": "生成已开始", "job": dict(self.job)}

    def tick(self, now=None):
        now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        day = now.date().isoformat()
        with self.lock:
            settings = self.store.settings()
            if (not settings["enabled"] or now.strftime("%H:%M") < settings["time"]
                    or trading_day(day, settings["closed_dates"]) is not True or self.job["state"] == "running"):
                return False
            ledger_path = self.store.root / "schedule-runs.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
            if day in ledger:
                return False
            # Persist before dispatch: restarts or repeated ticks cannot duplicate a run.
            ledger[day] = {"claimed_at": now.isoformat(), "time": settings["time"]}
            atomic_json(ledger_path, ledger)
            try:
                self.start({"date": day, "refresh": settings["refresh_quotes"]})
            except Exception as error:
                self.job = {"state": "error", "message": f"定时生成未启动：{str(error)[:200]}", "report_id": None}
            return True

    def start_scheduler(self):
        if self.scheduler and self.scheduler.is_alive():
            return
        def loop():
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception as error:
                    with self.lock:
                        self.job = {"state": "error", "message": f"调度检查失败：{str(error)[:200]}", "report_id": None}
                self.stop.wait(20)
        self.scheduler = threading.Thread(target=loop, name="review-clock", daemon=True)
        self.scheduler.start()

    def close(self):
        self.stop.set()
