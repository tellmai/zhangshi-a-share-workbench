"""Actual executions are facts, even when they violate the research plan."""
from __future__ import annotations

import csv
import io
import json
import secrets
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from pydantic import ValidationError

from .contracts import ResearchConfig, TradeInput, canonical, digest, money, timestamp, utc_now
from .data import SHANGHAI
from .repository import Conflict


def trade_day(stamp):
    return datetime.fromisoformat(timestamp(stamp)).astimezone(SHANGHAI).date().isoformat()


class Ledger:
    def __init__(self, repository):
        self.repo = repository

    def configuration(self):
        item = self.repo.get("settings", "research", {})
        return ResearchConfig.model_validate({k: v for k, v in item.items() if not k.startswith("_")}).model_dump()

    def account(self, name="manual"):
        if name not in ("manual", "shadow", "demo"):
            raise ValueError("未知账户")
        return self.repo.get("accounts", name, {"id": name, "opening_cash": self.configuration()["capital"], "confirmed_at": None})

    def trades(self, account="manual"):
        self.account(account)
        return sorted([x for x in self.repo.list("trades", 100000) if x["account"] == account and not x.get("voided")], key=lambda t: (t["executed_at"], t["id"]))

    def append_trade(self, value):
        row = TradeInput.model_validate(value).model_dump()
        account = self.account(row["account"])
        if "_revision" not in account:
            self.repo.put("accounts", row["account"], account, immutable=True)
        if row["executed_at"] > utc_now():
            raise ValueError("真实成交时间不能在未来")
        key = row.get("id") or (digest({"account": row["account"], "receipt": row["receipt"]})[:32] if row.get("receipt") else secrets.token_hex(16))
        expected = row.pop("expected_revision", None)
        row["id"] = key
        old = self.repo.get("trades", key)
        comparable = lambda x: {k: v for k, v in x.items() if not k.startswith("_") and k not in ("warnings", "recorded_at", "exit_plan")}
        if old:
            if comparable(old) == comparable(row):
                return old
            if expected is None:
                raise Conflict("成交编号已存在；更正需要提供当前版本号")
            if old["account"] != row["account"]:
                raise ValueError("更正不能改变账户归属")
        self.decorate_trade(row, old)
        return self.repo.put("trades", key, row, expected=expected)

    def decorate_trade(self, row, old=None, imported=False):
        warnings = []
        if imported:
            warnings.append("CSV导入实际成交，需核对计划与退出条件")
        signal = None
        if not row["signal_id"]:
            warnings.append("系统外交易")
        else:
            signal = self.repo.get("signals", row["signal_id"])
            if not signal or signal["code"] != row["code"]:
                warnings.append("关联信号不存在或证券不匹配")
            elif not signal.get("setup_passed"):
                warnings.append("未满足策略前置条件")
        if row["side"] == "buy" and row["qty"] % 100:
            warnings.append("买入股数不是100股整数倍，保留实际成交待核对")
        cfg = self.configuration()
        if row["side"] == "buy":
            if row["qty"] * row["price"] > cfg["capital"] * cfg["max_single_weight"]:
                warnings.append("超过默认研究单股金额上限，保留实际成交")
            if signal and row["qty"] > signal.get("budget", {}).get("shares", 0):
                warnings.append("超过关联信号的研究预算，保留实际成交")
            row["exit_plan"] = {"invalidation_price": row.get("invalidation_price") or (signal or {}).get("invalidation_price"),
                "holding_days": (old or {}).get("exit_plan", {}).get("holding_days", cfg["holding_days"]),
                "source": "人工填写" if row.get("invalidation_price") else "关联信号" if signal else "到期复核；价格条件待人工填写"}
        row["warnings"] = warnings
        row["recorded_at"] = utc_now()
        return row

    def void_trade(self, key, revision, reason):
        if not str(reason).strip():
            raise ValueError("作废须填写原因")
        old = self.repo.get("trades", key)
        if not old:
            raise ValueError("成交不存在")
        return self.repo.put("trades", key, {**old, "voided": True, "void_reason": str(reason)[:1000]}, expected=int(revision))

    def cash_entry(self, value):
        allowed = {"account", "amount", "at", "note", "receipt"}
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError("现金记录字段无效")
        account = value.get("account", "manual")
        self.account(account)
        info = self.account(account)
        if "_revision" not in info:
            self.repo.put("accounts", account, info, immutable=True)
        amount = money(value["amount"])
        if amount == 0 or abs(amount) > 100000000:
            raise ValueError("现金变动金额无效")
        stamp = timestamp(value.get("at") or utc_now())
        if stamp > utc_now():
            raise ValueError("现金变动时间不能在未来")
        receipt = value.get("receipt") or secrets.token_hex(12)
        key = digest({"account": account, "receipt": receipt})[:32]
        row = {"id": key, "account": account, "amount": float(amount), "at": stamp, "note": str(value.get("note", ""))[:1000], "receipt": receipt}
        return self.repo.put("cash", key, row, immutable=True)

    def portfolio(self, dataset=None, account="manual", asof=None):
        cfg, info = self.configuration(), self.account(account)
        cutoff = timestamp(asof or utc_now())
        day = trade_day(cutoff)
        rows = [t for t in self.trades(account) if t["executed_at"] <= cutoff]
        cashflows = sorted([x for x in self.repo.list("cash", 100000) if x["account"] == account and x["at"] <= cutoff], key=lambda x: (x["at"], x["id"]))
        cash = money(info["opening_cash"]) + sum((money(x["amount"]) for x in cashflows), Decimal(0))
        lots, realized, issues = defaultdict(list), [], []
        for t in rows:
            qty, price, fees = t["qty"], money(t["price"]), money(t["fees"])
            code = t["code"]
            if t["side"] == "buy":
                cash -= price * qty + fees
                lots[code].append({"qty": qty, "price": price, "fees": fees, "date": trade_day(t["executed_at"]), "trade_id": t["id"], "signal_id": t.get("signal_id"), "exit_plan": t.get("exit_plan", {})})
            else:
                cash += price * qty - fees
                needed, basis, buy_fees = qty, Decimal(0), Decimal(0)
                for lot in lots[code]:
                    if not needed:
                        break
                    take = min(needed, lot["qty"])
                    if take and lot["date"] == trade_day(t["executed_at"]):
                        issues.append(f"{code}实际成交存在T+1冲突，检查初始持仓或漏录")
                    original = lot["qty"]
                    consumed_fee = lot["fees"] * take / original if original else Decimal(0)
                    basis += lot["price"] * take
                    buy_fees += consumed_fee
                    lot["fees"] -= consumed_fee
                    lot["qty"] -= take
                    needed -= take
                if needed:
                    issues.append(f"{code}卖出{needed}股没有对应买入记录")
                realized.append({"trade_id": t["id"], "code": code, "qty": qty, "pnl": float(money(price * qty - fees - basis - buy_fees)) if not needed else None})
            if cash < 0:
                issues.append("现金出现负值，需核对入金、初始资金或漏录成交")
        marks = {}
        quote_date = None
        if dataset:
            quote_date = dataset["meta"]["trade_date"]
            if quote_date <= day:
                for s in dataset["stocks"]:
                    bars = [b for b in s.get("bars", []) if b["date"] <= day]
                    if bars:
                        marks[s["code"]] = (money(bars[-1]["close"]), bars[-1]["date"])
        positions, invested = [], Decimal(0)
        for code, items in sorted(lots.items()):
            active = [x for x in items if x["qty"] > 0]
            qty = sum(x["qty"] for x in active)
            if not qty:
                continue
            cost = sum((x["price"] * x["qty"] + x["fees"] for x in active), Decimal(0))
            mark, mark_day = marks.get(code, (cost / qty, None))
            if mark_day and mark_day < max(x["date"] for x in active):
                mark, mark_day = cost / qty, None
            calendar_known = bool((dataset or {}).get("meta", {}).get("calendar_verified"))
            past_sessions = [d for d in (dataset or {}).get("meta", {}).get("calendar", []) if d <= day]
            expected_quote = past_sessions[-1] if calendar_known and past_sessions else day
            stale = not mark_day or mark_day < max(x["date"] for x in active) or mark_day < expected_quote
            invested += mark * qty
            if stale:
                issues.append(f"{code}估值日期{mark_day}滞后，保留该日参考价格" if mark_day else f"{code}缺少买入后有效估值，暂以成本估值")
            groups = []
            for lot in active:
                signal = self.repo.get("signals", lot.get("signal_id")) if lot.get("signal_id") else None
                if signal:
                    groups.append(signal.get("overlap_group"))
            calendar = [d for d in (dataset or {}).get("meta", {}).get("calendar", []) if d <= day] if calendar_known else []
            entry = min(x["date"] for x in active)
            holding = sum(d > entry for d in calendar) if calendar else None
            positions.append({"code": code, "qty": qty, "sellable_qty": sum(x["qty"] for x in active if x["date"] < day),
                "cost": float(money(cost)), "mark_price": float(money(mark)), "mark_date": mark_day,
                "market_value": float(money(mark * qty)), "unrealized_pnl": float(money(mark * qty - cost)),
                "first_entry": entry, "holding_sessions": holding,
                "overdue": holding is not None and any(sum(d > x["date"] for d in calendar) >= x["exit_plan"].get("holding_days", cfg["holding_days"]) for x in active), "valuation_stale": stale or (mark_day != quote_date),
                "exit_plans": [{**x["exit_plan"], "qty": x["qty"], "entry_date": x["date"]} for x in active],
                "overlap_group": groups[0] if groups else None})
        identity = digest({"trades": [(t["id"], t["_revision"]) for t in rows], "cash": [(x["id"], x["_revision"]) for x in cashflows], "opening_cash": info["opening_cash"]})
        confirmed = info.get("ledger_digest") == identity and trade_day(info.get("confirmed_at") or "1970-01-01T00:00:00Z") == day
        equity = money(cash + invested)
        previous = [x for x in self.repo.list("valuations", 100000) if x["account"] == account and x["date"] <= day]
        contribution = sum(x["amount"] for x in cashflows)
        high_water = max([float(equity), float(info["opening_cash"])+contribution] + [x["equity"]+contribution-x.get("net_contributions", 0) for x in previous])
        return {"account": account, "asof": cutoff, "cash": float(money(cash)), "invested": float(money(invested)),
            "equity": float(equity), "high_water": high_water, "opening_cash": info["opening_cash"], "positions": positions,
            "realized": realized, "closed_pnl": float(money(sum((Decimal(str(t["pnl"])) for t in realized if t["pnl"] is not None), Decimal(0)))),
            "issues": list(dict.fromkeys(issues)), "incomplete": bool(issues), "confirmed": confirmed,
            "ledger_digest": identity, "trade_count": len(rows), "cashflows": cashflows, "quote_date": quote_date,
            "net_contributions": contribution,
            "net_return_available": not issues and cfg["fees_confirmed"],
            "note": "成交事实与研究信号独立；估值日期以逐仓标记为准"}

    def confirm(self, dataset, account="manual"):
        portfolio = self.portfolio(dataset, account)
        if portfolio["incomplete"]:
            raise ValueError("账本存在未解决问题，不能标为已核对")
        info = self.account(account)
        return self.repo.put("accounts", account, {**info, "confirmed_at": utc_now(), "ledger_digest": portfolio["ledger_digest"]})

    def preview_csv(self, text, account="manual"):
        self.account(account)
        if len(text) > 2000000:
            raise ValueError("成交CSV超过2MB")
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("CSV表头为空或重复")
        required = {"code", "side", "qty", "price", "executed_at"}
        if not required.issubset(reader.fieldnames):
            raise ValueError("CSV需要code,side,qty,price,executed_at，可选fees,note,receipt,signal_id")
        if set(reader.fieldnames) - required - {"fees", "note", "receipt", "signal_id", "invalidation_price"}:
            raise ValueError("CSV包含不支持或歧义列")
        rows, errors = [], []
        for index, row in enumerate(reader, 2):
            if index > 10001:
                raise ValueError("单次最多导入10000条成交")
            try:
                if None in row:
                    raise ValueError("列数不匹配")
                parsed = TradeInput.model_validate({**{k: v for k, v in row.items() if v not in (None, "")},
                    "account": account, "qty": int(row["qty"]), "price": float(row["price"]), "fees": float(row.get("fees") or 0)}).model_dump()
                if parsed["executed_at"] > utc_now():
                    raise ValueError("成交日期在未来")
                parsed.pop("expected_revision", None)
                rows.append(parsed)
            except ValidationError as error:
                errors.append({"line": index, "error": "；".join(".".join(map(str, e["loc"]))+": "+e["msg"] for e in error.errors(include_url=False))[:400]})
            except (ValueError, TypeError, KeyError) as error:
                errors.append({"line": index, "error": str(error)[:400]})
        key = digest({"account": account, "rows": rows, "errors": errors})[:32]
        result = {"id": key, "account": account, "rows": rows, "errors": errors, "can_commit": bool(rows) and not errors}
        self.repo.put("trade_imports", key, result, immutable=True)
        return result

    def commit_csv(self, preview_id):
        preview = self.repo.get("trade_imports", preview_id)
        if not preview or not preview["can_commit"]:
            raise ValueError("请先修复CSV错误并重新预览")
        info = self.account(preview["account"])
        if "_revision" not in info:
            self.repo.put("accounts", preview["account"], info, immutable=True)
        # One transaction for the whole validated preview, including deduplication.
        added = 0
        with self.repo.connect(write=True) as db:
            for i, item in enumerate(preview["rows"]):
                row = dict(item)
                receipt = row.get("receipt") or f"import:{preview_id}:{i}"
                key = digest({"account": row["account"], "receipt": receipt})[:32]
                row.update(id=key, receipt=receipt)
                self.decorate_trade(row, imported=True)
                old = db.execute("SELECT payload FROM documents WHERE collection='trades' AND key=? ORDER BY revision DESC LIMIT 1", (key,)).fetchone()
                if old:
                    prior = json.loads(old["payload"])
                    compare = lambda x: {k: v for k, v in x.items() if k not in ("warnings", "recorded_at", "exit_plan")}
                    if compare(prior) != compare(row):
                        raise Conflict("重复回执内容不同，整批未写入")
                    continue
                db.execute("INSERT INTO documents VALUES('trades',?,1,?,?)", (key, utc_now(), canonical(row).decode()))
                self.repo.audit("csv_trade", key, {"preview": preview_id}, db)
                added += 1
        return {"added": added, "total": len(preview["rows"]), "preview_id": preview_id}
