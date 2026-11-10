"""Reviewable per-user Windows Task Scheduler definitions, disabled by default."""
from __future__ import annotations

import getpass
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from .contracts import digest, utc_now

TIMES = {"collect": "16:30", "retry": "17:30", "report": "19:30", "backfill": "20:30", "backup": "21:30"}


def task_definitions(root):
    root = Path(root).resolve()
    prefix = "Zhangshi-" + digest(str(root))[:10]
    executable = str(Path(sys.executable).parent / "ZhangshiWorker.exe") if getattr(sys, "frozen", False) else sys.executable
    script = [] if getattr(sys, "frozen", False) else [str(Path(__file__).resolve().parents[1]/"scripts"/"workbench.py")]
    initial = ["--workbench-worker"] if getattr(sys, "frozen", False) else []
    today = datetime.now(timezone.utc).date().isoformat()
    definitions = []
    for kind, clock in TIMES.items():
        args = initial + script + ["--data-dir", str(root), "due", "--kind", kind]
        hour, minute = map(int, clock.split(":"))
        start = datetime.fromisoformat(today).replace(tzinfo=timezone.utc, hour=hour, minute=minute) - timedelta(hours=8)
        definitions.append({"name": prefix+"-"+kind, "kind": kind, "time_shanghai": clock, "start_utc": start.isoformat(),
            "command": executable, "arguments": subprocess.list2cmdline(args), "working_directory": str(Path(executable).parent),
            "user": getpass.getuser(), "description": "涨势盘后研究任务；电脑须唤醒且用户已登录；不下单"})
    return definitions


def task_xml(definition):
    ns = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    ET.register_namespace("", ns)
    def node(parent, tag, value=None, **attrs):
        item = ET.SubElement(parent, "{"+ns+"}"+tag, attrs)
        if value is not None:
            item.text = str(value)
        return item
    task = ET.Element("{"+ns+"}Task", {"version": "1.4"})
    registration = node(task, "RegistrationInfo")
    node(registration, "Description", definition["description"])
    triggers = node(task, "Triggers")
    trigger = node(triggers, "CalendarTrigger")
    node(trigger, "StartBoundary", definition["start_utc"])
    node(trigger, "Enabled", "true")
    node(node(trigger, "ScheduleByDay"), "DaysInterval", 1)
    principals = node(task, "Principals")
    principal = node(principals, "Principal", id="Author")
    node(principal, "UserId", definition["user"])
    node(principal, "LogonType", "InteractiveToken")
    node(principal, "RunLevel", "LeastPrivilege")
    settings = node(task, "Settings")
    for tag, value in (("MultipleInstancesPolicy", "IgnoreNew"), ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"), ("StartWhenAvailable", "true"), ("RunOnlyIfNetworkAvailable", "false"),
        ("Enabled", "true"), ("Hidden", "true"), ("WakeToRun", "false"), ("ExecutionTimeLimit", "PT2H")):
        node(settings, tag, value)
    action = node(node(task, "Actions", Context="Author"), "Exec")
    node(action, "Command", definition["command"])
    node(action, "Arguments", definition["arguments"])
    node(action, "WorkingDirectory", definition["working_directory"])
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def configure(repository, enabled):
    if type(enabled) is not bool:
        raise ValueError("调度开关须为布尔值")
    if os.name != "nt":
        raise ValueError("此部署使用Windows计划任务")
    folder = repository.home/"scheduler"
    folder.mkdir(parents=True, exist_ok=True)
    definitions, results = task_definitions(repository.root), []
    for definition in definitions:
        if enabled:
            path = folder/(definition["name"]+".xml")
            path.write_bytes(task_xml(definition))
            command = ["schtasks", "/Create", "/TN", definition["name"], "/XML", str(path), "/F"]
        else:
            command = ["schtasks", "/Delete", "/TN", definition["name"], "/F"]
        proc = subprocess.run(command, capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        results.append({"name": definition["name"], "ok": proc.returncode == 0,
            "detail": (proc.stdout+proc.stderr).decode("mbcs", errors="replace")[:1000]})
    actual = enabled and all(r["ok"] for r in results)
    record = {"enabled": actual, "requested_enabled": enabled, "updated_at": utc_now(), "definitions": definitions, "results": results,
        "note": "关闭界面后独立运行；休眠期间不运行，恢复后检查漏采。"}
    repository.put("settings", "scheduler", record)
    if enabled and not actual:
        raise ValueError("部分计划任务未能注册，详情已保存；请在任务页查看")
    return record
