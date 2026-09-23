from investment_lab.common import dec

NAMES = {"buy_hold": "买入持有", "dca": "定期投入", "moving_average": "均线", "drawdown_buy": "回撤买入", "rotation": "同市场轮动", "leverage_rebalance": "杠杆再平衡", "futures_roll": "月份合约换月", "cash": "持有现金"}


class Builtin:
    def __init__(self, name):
        if name not in NAMES:
            raise ValueError("未知内置策略")
        self.name = name

    def on_session(self, ctx):
        p, state = ctx.params, ctx.state
        symbol = ctx.symbols[0]
        weight = dec(p.get("weight", "0.95"))
        if self.name == "buy_hold":
            if not state.get("ordered"):
                ctx.order_target_weight(symbol, weight, "初始配置后持有")
                state["ordered"] = True
        elif self.name == "dca":
            month = ctx.date[:7]
            if state.get("month") != month:
                ctx.order_value(symbol, min(ctx.cash, dec(p.get("amount", 1000))), "每月首次交易日投入")
                state["month"] = month
        elif self.name == "moving_average":
            window = int(p.get("window", 20))
            history = ctx.history(symbol, window)
            if len(history) == window:
                target = weight if history[-1] > sum(history) / window else 0
                ctx.order_target_weight(symbol, target, "已知收盘价与历史均线比较")
        elif self.name == "drawdown_buy":
            history = ctx.history(symbol, int(p.get("window", 60)))
            if history and history[-1] / max(history) - 1 <= -dec(p.get("threshold", "0.1")):
                ctx.order_target_weight(symbol, weight, "历史高点回撤达到阈值")
        elif self.name == "rotation":
            window = int(p.get("window", 20))
            scores = {}
            for candidate in ctx.symbols:
                prices = ctx.history(candidate, window)
                if len(prices) == window:
                    scores[candidate] = prices[-1] / prices[0] - 1
            if scores and state.get("month") != ctx.date[:7]:
                winner = max(scores, key=scores.get)
                for candidate in ctx.symbols:
                    if candidate != winner:
                        ctx.order_target_weight(candidate, 0, "月度轮动退出")
                ctx.order_target_weight(winner, weight, "月度历史动量轮动")
                state["month"] = ctx.date[:7]
        elif self.name == "leverage_rebalance":
            ctx.order_target_weight(symbol, dec(p.get("weight", "1.5")), "每日恢复目标杠杆，包含交易成本")
        elif self.name == "futures_roll":
            # Explicit calendar schedule; no future volume is inspected.
            schedule = p.get("roll_schedule", {})
            eligible = [d for d in schedule if d <= ctx.date]
            if eligible:
                desired = schedule[max(eligible)]
                if desired != state.get("contract"):
                    for candidate, quantity in ctx.positions.items():
                        if quantity:
                            ctx.order_shares(candidate, -quantity, "预先固定日期平多换月")
                    ctx.order_shares(desired, int(p.get("contracts", 1)), "预先固定日期开下一月份多头")
                    state["contract"] = desired
