"""Shared causal price/volume primitives for legacy and V2 consumers."""
from __future__ import annotations


def adjusted_series(bars, field="close", anchor=None):
    if not bars or any(b.get("adj_factor") is None for b in bars):
        return None
    anchor = bars[-1]["adj_factor"] if anchor is None else anchor
    if not anchor:
        return None
    return [bar[field] * bar["adj_factor"] / anchor for bar in bars]


def preceding_volume_mean(bars, period=20):
    if len(bars) < period+1:
        return None
    return sum(b["volume_shares"] for b in bars[-period-1:-1]) / period
