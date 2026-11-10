"""Integer-lot research budgets using cents and explicit execution assumptions."""
from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR

from .contracts import ResearchConfig, money
from .market_rules import dated_cost, trading_rule


def budget(code, day, entry, invalidation, config=None, portfolio=None, regime="STRONG", overlap_group=None):
    cfg = ResearchConfig.model_validate(config or {}).model_dump()
    p = portfolio or {}
    equity = money(p.get("equity", cfg["capital"]))
    cash = money(p.get("cash", cfg["capital"]))
    invested = money(p.get("invested", 0))
    positions = p.get("positions", [])
    reasons = []
    price, stop = money(entry), money(invalidation)
    if price <= 0 or stop <= 0 or stop >= price:
        reasons.append("参考价与失效价无有效正向风险价差")
    if regime in ("WEAK", "UNCERTAIN"):
        reasons.append("市场环境不允许新增计划")
    if p.get("incomplete"):
        reasons.append("账户账本不完整，需先核对现金与成交")
    if len(positions) >= cfg["max_positions"]:
        reasons.append("已达到最大持仓数")
    if any(x.get("code") == code for x in positions):
        reasons.append("已持有该证券，不生成摊平计划")
    if overlap_group and any(x.get("overlap_group") == overlap_group for x in positions):
        reasons.append("同一主线重叠组已有持仓")
    high_water = money(p.get("high_water", equity))
    if high_water > 0 and 1 - equity / high_water >= Decimal(str(cfg["drawdown_review"])):
        reasons.append("达到回撤复核线，暂停新增计划")
    lot = trading_rule(code, day)["lot"]
    fraction = min(cfg["max_total_weight"], .25 if regime == "ROTATION" else cfg["max_total_weight"])
    cap = min(equity * Decimal(str(cfg["max_single_weight"])),
              max(Decimal(0), equity * Decimal(str(fraction)) - invested), cash)
    risk_cash = money(equity * Decimal(str(cfg["risk_fraction"])))
    qty = 0
    if not reasons and cap > 0:
        loss_per_share = price - stop
        initial = min(cap / price, risk_cash / loss_per_share)
        qty = int((initial / lot).to_integral_value(rounding=ROUND_FLOOR)) * lot
        while qty > 0:
            buy = dated_cost(price * qty, "buy", day, cfg)
            sell = dated_cost(stop * qty, "sell", day, cfg)
            paid = price * qty + money(sum(buy.values()))
            loss = (price - stop) * qty + money(sum(buy.values()) + sum(sell.values()))
            if paid <= cap and paid <= cash and loss <= risk_cash:
                break
            qty -= lot
    if qty <= 0 and not reasons:
        reasons.append("资金、费用或计划风险不足一手")
    buy = dated_cost(price * qty, "buy", day, cfg) if qty else {}
    sell = dated_cost(stop * qty, "sell", day, cfg) if qty else {}
    return {"code": code, "shares": qty, "entry_reference": float(price), "invalidation_price": float(stop),
        "notional": float(money(price * qty)), "estimated_entry_cost": float(money(price * qty + money(sum(buy.values())))),
        "planned_risk": float(money((price-stop) * qty + money(sum(buy.values()) + sum(sell.values())))) if qty else 0,
        "risk_budget": float(risk_cash), "status": "OBSERVE" if qty else "NOT_EXECUTABLE_FOR_CAPITAL" if reasons == ["资金、费用或计划风险不足一手"] else "BLOCKED",
        "reasons": reasons, "hypothetical_account": not (cfg["account_confirmed"] and p.get("confirmed")),
        "fees_confirmed": cfg["fees_confirmed"], "warning": "计划风险不限制跳空、停牌或跌停造成的实际损失"}
