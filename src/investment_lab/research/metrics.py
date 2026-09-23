import math
from datetime import date
import numpy as np


def xirr(flows):
    if not any(v < 0 for _, v in flows) or not any(v > 0 for _, v in flows):
        return None
    first = flows[0][0]
    times = [(d - first).days / 365.25 for d, _ in flows]
    def npv(rate):
        return sum(v / (1 + rate) ** t for (_, v), t in zip(flows, times))
    # Return missing when the bounded range has no unique detected bracket.
    grid = [-0.9999, -0.99, -0.9, -0.5, 0, 0.1, 0.5, 1, 2, 5, 10, 100, 10000]
    brackets = [(a, b) for a, b in zip(grid, grid[1:]) if npv(a) * npv(b) < 0]
    if npv(0) == 0:
        return 0.0
    if len(brackets) != 1:
        return None
    low, high = brackets[0]
    for _ in range(120):
        mid = (low + high) / 2
        if npv(low) * npv(mid) <= 0:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def metrics(curve, trades, initial, realized):
    previous, index, peak, drawdown = float(initial), 1.0, 1.0, 0.0
    returns, flows = [], [(date.fromisoformat(curve[0]["date"]), -float(initial))]
    for row in curve:
        equity, flow = float(row["equity"]), float(row["flow"])
        denominator = previous + flow  # external flows credited before the session's exposure
        daily = equity / denominator - 1 if denominator > 0 else None
        if daily is not None:
            index *= 1 + daily
            returns.append(daily)
        peak = max(peak, index)
        drawdown = min(drawdown, index / peak - 1)
        row["twr_index"], row["drawdown"] = index, index / peak - 1
        previous = equity
        if flow:
            flows.append((date.fromisoformat(row["date"]), -flow))
    end = date.fromisoformat(curve[-1]["date"])
    flows.append((end, previous))
    years = (end - date.fromisoformat(curve[0]["date"])).days / 365.25
    std = float(np.std(returns, ddof=1)) if len(returns) > 2 else None
    annualized = index ** (1 / years) - 1 if years >= 1 and index > 0 else None
    fees = sum(float(t["fee"]) for t in trades)
    net_flows = sum(float(r["flow"]) for r in curve)
    return {"total_return": index - 1, "annualized_return": annualized, "max_drawdown": drawdown,
            "volatility": std * math.sqrt(252) if std is not None else None,
            "sharpe_zero_rf": float(np.mean(returns)) / std * math.sqrt(252) if std and len(returns) >= 20 else None,
            "money_weighted_return": xirr(flows) if years >= 1 else None,
            "net_profit": previous - float(initial) - net_flows, "net_flows": net_flows, "final_equity": previous,
            "trades": len(trades), "win_rate": sum(v > 0 for v in realized) / len(realized) if realized else None,
            "fees": fees, "interest": float(curve[-1]["interest"]),
            "turnover": sum(float(t["notional"]) for t in trades) / float(np.mean([float(r["equity"]) for r in curve])) if all(float(r["equity"]) > 0 for r in curve) else None}
