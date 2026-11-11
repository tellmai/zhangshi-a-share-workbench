"""V2 contracts. Missing historical knowledge stays missing; all clocks are aware."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def timestamp(value):
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def money(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("金额必须是有限数值")
    return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def symbol(value):
    text = str(value).strip().upper()
    if re.fullmatch(r"(?:SH|SZ)\d{6}", text):
        text = text[2:] + "." + text[:2]
    if re.fullmatch(r"\d{6}", text):
        text += ".SH" if text.startswith("6") else ".SZ"
    if not re.fullmatch(r"(?:6\d{5}\.SH|[03]\d{5}\.SZ|DEMO\d+)", text):
        raise ValueError("仅支持沪深证券完整代码")
    return text


def board_of(code):
    code = symbol(code)
    if code.startswith("DEMO"):
        return "DEMO"
    if code.startswith("688"):
        return "STAR"
    if code.startswith(("300", "301")):
        return "CHINEXT"
    if re.fullmatch(r"(?:60[0135]\d{3}\.SH|00[0123]\d{3}\.SZ)", code):
        return "MAIN"
    return "OTHER"


def in_universe(stock, boards=("MAIN", "CHINEXT"), demo=False):
    try:
        board = board_of(stock["code"])
    except (ValueError, KeyError):
        return False
    return (board in boards or (demo and board == "DEMO")) and not (
        stock.get("is_st") or "ST" in stock.get("name", "").upper() or "退" in stock.get("name", ""))


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True, validate_default=True)


class Provenance(Contract):
    provider_id: str = Field(min_length=1, max_length=120)
    source_family: str = Field(min_length=1, max_length=120)
    adapter_version: str = "1.0.0"
    event_time: str
    available_at: str | None = None
    ingested_at: str = Field(default_factory=utc_now)
    known_at: str | None = None
    evidence_digest: str | None = None
    precision: Literal["observed", "documented", "unknown", "synthetic"] = "unknown"
    verified_fields: list[str] = Field(default_factory=list)

    @field_validator("event_time", "available_at", "ingested_at", "known_at")
    @classmethod
    def aware_time(cls, value):
        return timestamp(value) if value else None


class ResearchConfig(Contract):
    capital: float = Field(default=20000, gt=0, le=100000000, strict=True)
    risk_fraction: float = Field(default=.005, gt=0, le=.05, strict=True)
    max_single_weight: float = Field(default=.25, gt=0, le=1, strict=True)
    max_total_weight: float = Field(default=.50, gt=0, le=1, strict=True)
    max_positions: int = Field(default=2, ge=1, le=20, strict=True)
    holding_days: int = Field(default=5, ge=1, le=30, strict=True)
    drawdown_review: float = Field(default=.05, gt=0, le=.5, strict=True)
    boards: list[Literal["MAIN", "CHINEXT"]] = Field(default_factory=lambda: ["MAIN", "CHINEXT"])
    commission_bps: float = Field(default=3, ge=0, le=100)
    minimum_commission: float = Field(default=5, ge=0, le=100)
    transfer_bps: float | None = Field(default=None, ge=0, le=100)
    slippage_bps: float = Field(default=5, ge=0, le=300)
    fees_confirmed: bool = False
    account_confirmed: bool = False
    breadth_strong: float = Field(default=.65, ge=0, le=1)
    breadth_rotation: float = Field(default=.4, ge=0, le=1)
    limit_density: float = Field(default=.025, ge=0, le=1)
    overlap_threshold: float = Field(default=.8, gt=0, le=1)

    @model_validator(mode="after")
    def consistent(self):
        if not self.boards or len(set(self.boards)) != len(self.boards):
            raise ValueError("研究板块不能为空或重复")
        if self.max_single_weight > self.max_total_weight:
            raise ValueError("单股上限不能大于总仓位上限")
        if self.breadth_rotation > self.breadth_strong:
            raise ValueError("轮动阈值不能高于强势阈值")
        return self


class TradeInput(Contract):
    id: str | None = None
    account: Literal["manual", "shadow", "demo"] = "manual"
    code: str
    side: Literal["buy", "sell"]
    qty: int = Field(gt=0, le=100000000, strict=True)
    price: float = Field(gt=0, le=1000000)
    fees: float = Field(default=0, ge=0, le=10000000)
    executed_at: str
    signal_id: str | None = None
    invalidation_price: float | None = Field(default=None, gt=0, le=1000000)
    note: str = Field(default="", max_length=2000)
    receipt: str | None = Field(default=None, max_length=160)
    violations: list[str] = Field(default_factory=list)
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator("side", mode="before")
    @classmethod
    def normalize_side(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value):
        return symbol(value)

    @field_validator("executed_at")
    @classmethod
    def normalize_time(cls, value):
        return timestamp(value)


class HistoryRequest(Contract):
    code: str
    start: date
    end: date
    warmup: int = Field(default=120, ge=0, le=1000)
    reason: Literal["seed", "position", "watch", "repair"] = "seed"

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value):
        return symbol(value)

    @model_validator(mode="after")
    def ordered(self):
        if self.start > self.end:
            raise ValueError("历史起始日期晚于结束日期")
        return self


def load_config(text):
    """One strong schema for JSON and safe YAML; no executable strategy expressions."""
    if len(text) > 200000:
        raise ValueError("配置文件过大")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        import yaml
        value = yaml.safe_load(text)
    return ResearchConfig.model_validate(value).model_dump()
