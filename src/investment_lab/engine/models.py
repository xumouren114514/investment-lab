from dataclasses import dataclass, field
from decimal import Decimal
from investment_lab.common import dec
from .cash_flows import validate_cash_flows


@dataclass
class Config:
    start: str
    end: str
    symbols: list[str]
    initial_cash: str = "100000"
    mode: str = "strict_vwap"
    commission_bps: str = "3"
    minimum_commission: str = "0"
    sell_tax_bps: str = "0"
    slippage_bps: str = "5"
    max_participation: str = "0.01"
    max_leverage: str = "1"
    annual_interest: str = "0.06"
    maintenance_equity_ratio: str = "0.25"
    cash_settlement_days: int = 0
    order_lifetime: int = 1
    warmup: int = 0
    cash_flows: dict[str, str | int | float] = field(default_factory=dict)
    rules: list[dict] = field(default_factory=list)
    rate_curve: dict[str, str] = field(default_factory=dict)
    benchmark: str | None = None
    benchmark_strategy: str = "buy_hold"
    seed: int = 42
    allow_unverified_actions: bool = False
    assumptions: str = "自定义研究费率、融资及现金结算假设；不代表历史券商规则"

    def __post_init__(self):
        from datetime import date
        date.fromisoformat(self.start)
        date.fromisoformat(self.end)
        if self.start >= self.end or not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("需要非重复证券和至少两个日期")
        if self.mode not in ("strict_vwap", "estimated_vwap", "close_research", "reference_research"):
            raise ValueError("未知价格模式")
        if self.benchmark_strategy not in ("buy_hold", "dca", "cash"):
            raise ValueError("基准策略无效")
        for name in ("commission_bps", "minimum_commission", "sell_tax_bps", "slippage_bps", "annual_interest"):
            if dec(getattr(self, name)) < 0:
                raise ValueError(name + " 不得为负")
        if dec(self.initial_cash) <= 0 or not 1 <= dec(self.max_leverage) <= 10:
            raise ValueError("初始资金须为正，研究杠杆范围 1 到 10")
        if not 0 < dec(self.max_participation) <= 1 or not 0 < dec(self.maintenance_equity_ratio) <= 1:
            raise ValueError("参与率/维持权益比例须在 (0,1]")
        if self.order_lifetime < 1 or self.warmup < 0 or self.cash_settlement_days < 0:
            raise ValueError("交易日参数无效")
        validate_cash_flows(self.cash_flows)


@dataclass
class Order:
    id: int
    symbol: str
    quantity: Decimal
    remaining: Decimal
    signal_date: str
    reason: str
    visible: dict
    age: int = 0
    forced: bool = False
    odd_lot_liquidation: bool = False
