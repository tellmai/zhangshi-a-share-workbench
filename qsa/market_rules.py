"""Dated mainland equity rules; provider limits take precedence over estimates."""
from __future__ import annotations

from decimal import Decimal

from .contracts import board_of, money


def trading_rule(code, day, is_st=False, listing_session=None):
    board = board_of(code)
    if board not in ("MAIN", "CHINEXT", "DEMO"):
        raise ValueError("证券不在主板与创业板规则范围")
    if listing_session is not None and listing_session <= 5:
        return {"id": "new-listing-special", "lot": 100, "tick": .01, "sell_delay": 1,
                "limit_ratio": None, "excluded": True}
    ratio = .20 if board == "CHINEXT" and day >= "2020-08-24" else .10
    if is_st and board != "CHINEXT":
        ratio = .10 if day >= "2026-07-06" else .05
    return {"id": f"{board}:{'st' if is_st else 'normal'}:{ratio}", "lot": 100, "tick": .01,
            "sell_delay": 1, "limit_ratio": ratio, "excluded": bool(is_st)}


def dated_cost(notional, side, day, config):
    if day < "2008-09-19":
        raise ValueError("当前费用规则不覆盖2008-09-19以前")
    n = Decimal(str(notional))
    stamp = Decimal("5" if day >= "2023-08-28" else "10")
    transfer = config.get("transfer_bps")
    if transfer is None and day < "2022-04-29":
        raise ValueError("默认过户费日期表覆盖2022-04-29起；更早区间需要显式配置已核对的研究费率")
    if transfer is None:
        transfer = .1 if day >= "2022-04-29" else .2
    bps = Decimal(10000)
    result = {
        "commission": money(max(Decimal(str(config.get("minimum_commission", 5))), n * Decimal(str(config.get("commission_bps", 3))) / bps)),
        "stamp": money(n * stamp / bps) if side == "sell" else Decimal(0),
        "transfer": money(n * Decimal(str(transfer)) / bps),
        "slippage": money(n * Decimal(str(config.get("slippage_bps", 5))) / bps),
    }
    return {k: float(v) for k, v in result.items()}


def visible_state(stock, day, decision_time):
    from .contracts import timestamp
    states = [s for s in stock.get("status_history", []) if s.get("known_at")
        and timestamp(s["known_at"]) <= timestamp(decision_time)
        and s.get("valid_from", "9999") <= day and (not s.get("valid_to") or day < s["valid_to"])]
    return max(states, key=lambda s: (timestamp(s["known_at"]), s["valid_from"])) if states else None
