from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal
import numpy as np

from investment_lab.engine.core import prepare_bars, simulate
from investment_lab.engine.reference import REFERENCE_MODE, REFERENCE_WARNING
from investment_lab.engine.cash_flows import MONTHLY_RULE


def research_metadata(config):
    metadata = {"monthly_deposit_rule": MONTHLY_RULE} if "monthly" in config.cash_flows else {}
    if config.mode == REFERENCE_MODE:
        metadata.update(reference_only=True, normal_ranking_eligible=False,
                        research_warning=REFERENCE_WARNING, point_in_time_prices_verified=False)
    return metadata


class _RebalancedBenchmark:
    """C-119：买入持有的基准也要把新增现金（月度入金/一次性入金）投进去。

    原来 `buy_hold` 只在下单一次，后续入金永远留在现金里，长期会把基准收益严重压低、
    把策略的超额收益抬高。这里：首次按目标权重买入；之后有现金且没有在途订单时补投。
    """

    def __init__(self, weight="1"):
        self.weight = Decimal(str(weight))

    def on_session(self, ctx):
        symbol = ctx.symbols[0] if ctx.symbols else None
        if not symbol:
            return
        if any((order or {}).get("symbol") == symbol for order in (ctx.pending or ())):
            return
        if not ctx.state.get("ordered"):
            ctx.order_target_weight(symbol, self.weight, "基准初始配置")
            ctx.state["ordered"] = True
            return
        equity = ctx.equity if ctx.equity > 0 else Decimal("1")
        if ctx.cash > equity * Decimal("0.001"):   # 忽略手续费找零，避免天天买一分钱
            ctx.order_value(symbol, ctx.cash, "基准投入新增现金")


def benchmark_result(manifest, bars, config, progress=None):
    if not config.benchmark:
        return None
    other = replace(config, symbols=[config.benchmark], benchmark=None, max_leverage="1")
    result = simulate(manifest, bars, other, _RebalancedBenchmark("1"), {"weight": "1"}, progress)
    # C-112/113：基准仓位必须在 metadata 里披露；已从固定 95% 改为 100% 目标仓位（整股/手续费自然留少量现金）
    result.setdefault("metadata", {})["benchmark_weight"] = "1"
    result["metadata"]["benchmark_note"] = ("基准按 100% 目标仓位买入并持有；整股取整与手续费会留下少量现金，"
                                            "这部分现金不计收益")
    return result


def _trust_summary(samples) -> dict:
    """C-159：把各窗口的「可信度」字段汇总到滚动结果的顶层 metadata。

    每个完成的窗口都带 `price_limit_guard` 等字段；汇总层不给的话，结果页的可信度块就是空的。
    汇总口径说明白：守卫取"所有窗口的最差状态"，携转比例按窗口合计，门禁各窗口应一致。
    """
    from collections import Counter

    guards, gates, flat, bars_total = [], [], 0, 0
    for sample in samples:
        if sample.get("status") != "completed":
            continue
        meta = (sample.get("result") or {}).get("metadata") or {}
        guard = meta.get("price_limit_guard")
        if guard:
            guards.append(guard)
        gate = meta.get("corporate_action_gate")
        if gate:
            gates.append(gate)
        for item in (meta.get("warmup_bars") or {}).values():
            flat += int(item.get("flat") or 0)
            bars_total += int(item.get("bars") or 0)
    out = {}
    if guards:
        counts = Counter(g["status"] for g in guards)
        if counts.get("unavailable") == len(guards):
            status = "unavailable"
        elif counts.get("applied") == len(guards):
            status = "applied"
        else:
            status = "partial"
        sources = sorted({g.get("source") for g in guards if g.get("source")})
        out["price_limit_guard"] = {
            "status": status, "windows": dict(counts),
            "applied": sum(int(g.get("applied") or 0) for g in guards),
            "skipped": sum(int(g.get("skipped") or 0) for g in guards),
            "source": sources[0] if len(sources) == 1 else "mixed",
            "note": f"{len(guards)} 个窗口汇总；明细见各窗口结果"}
    if gates:
        out["corporate_action_gate"] = gates[0] if len(set(gates)) == 1 else "mixed"
    if bars_total:
        out["warmup_flat_summary"] = (f"{len(guards)} 个窗口合计 携转 {flat}/{bars_total}"
                                      f"（{flat / bars_total:.1%}）")
    return out


def rolling(manifest, bars, config, factory, params, interval="month", horizon=60, end_mode="fixed_length", progress=None):
    if interval not in ("day", "month", "quarter", "year") or end_mode not in ("fixed_length", "common_end") or horizon < 2:
        raise ValueError("滚动参数无效")
    # Index the frozen bars once; every window and benchmark reuses the same lookups.
    bars = prepare_bars(bars)
    days = [d for d in manifest["sessions"] if config.start <= d <= config.end]
    starts, seen = [], set()
    for i, d in enumerate(days):
        key = d if interval == "day" else d[:7] if interval == "month" else f"{d[:4]}-{(int(d[5:7])-1)//3}" if interval == "quarter" else d[:4]
        if key not in seen:
            starts.append((i, d))
            seen.add(key)
    end_by_index = {}
    for i, _ in starts:
        end_i = i + horizon - 1 if end_mode == "fixed_length" else len(days) - 1
        if i < end_i < len(days):
            end_by_index[i] = end_i
    passes_per_window = 2 if config.benchmark else 1
    total_work = sum((end_i - i + 1) * passes_per_window for i, end_i in end_by_index.items())
    day_index = {day: i for i, day in enumerate(days)}
    completed_work = 0
    samples = []
    for i, start in starts:
        end_i = end_by_index.get(i)
        if end_i is None or end_i <= i:
            samples.append({"start": start, "status": "skipped", "reason": "持有期不足"})
            continue
        window_length = end_i - i + 1
        window_offset = completed_work
        # Schedule is translated by relative session offset, keeping identical contribution rules.
        base_flows = {day_index[d]: amount for d, amount in config.cash_flows.items() if d in day_index}
        flows = {days[i + offset]: amount for offset, amount in base_flows.items() if i + offset <= end_i}
        # Recurring calendar-month deposits are resolved inside each window,
        # separately from the legacy relative-session one-off schedule.
        if "monthly" in config.cash_flows:
            flows["monthly"] = config.cash_flows["monthly"]
        round_config = replace(config, start=start, end=days[end_i], cash_flows=flows)
        try:
            run_progress = (lambda done, _total, offset=window_offset: progress(offset + done, total_work)) if progress else None
            result = simulate(manifest, bars, round_config, factory(), deepcopy(params), run_progress)
            completed_work += window_length
            benchmark_offset = completed_work
            benchmark_progress = (lambda done, _total, offset=benchmark_offset: progress(offset + done, total_work)) if progress and config.benchmark else None
            benchmark = benchmark_result(manifest, bars, round_config, benchmark_progress)
            if config.benchmark:
                completed_work += window_length
            sample = {"start": start, "end": days[end_i], "sessions": end_i - i + 1,
                      "status": "completed", "metrics": result["metrics"],
                      "benchmark": benchmark["metrics"] if benchmark else None,
                      "relative_return": result["metrics"]["total_return"] - benchmark["metrics"]["total_return"] if benchmark else None,
                      "result": result}
        except ValueError as exc:
            completed_work = window_offset + window_length * passes_per_window
            sample = {"start": start, "end": days[end_i], "status": "skipped", "reason": str(exc)}
        samples.append(sample)
        if progress:
            progress(completed_work, total_work)
    values = [s["metrics"]["total_return"] for s in samples if s["status"] == "completed"]
    drawdowns = [s["metrics"]["max_drawdown"] for s in samples if s["status"] == "completed"
                 and isinstance(s.get("metrics"), dict) and isinstance(s["metrics"].get("max_drawdown"), (int, float))]
    if not values:
        raise ValueError("全部滚动窗口被跳过: " + "; ".join(s.get("reason", "") for s in samples[:4]))
    lengths = [s["sessions"] for s in samples if s["status"] == "completed" and isinstance(s.get("sessions"), int)]
    relatives = [s["relative_return"] for s in samples
                 if s["status"] == "completed" and isinstance(s.get("relative_return"), (int, float))]
    return {"kind": "rolling", "samples": samples, "summary": {"count": len(values), "skipped": len(samples) - len(values),
            "window_sessions_min": min(lengths) if lengths else None,
            "window_sessions_max": max(lengths) if lengths else None,
            "loss_ratio": sum(v < 0 for v in values) / len(values), "median": float(np.median(values)),
            "q10": float(np.quantile(values, .1)), "q90": float(np.quantile(values, .9)), "best": max(values), "worst": min(values),
            # C-108：列表「最大回撤」列原来装的是“最差窗口收益”，不是回撤；这里补真实回撤聚合
            "worst_drawdown": min(drawdowns) if drawdowns else None,
            "median_drawdown": float(np.median(drawdowns)) if drawdowns else None,
            # C-120：SPEC 要求的“相对基准表现”在滚动里也要能看到（基准与各窗口同资金流）
            "relative_median": float(np.median(relatives)) if relatives else None},
            "metadata": {**research_metadata(config), **_trust_summary(samples), "interval": interval,
                         # C-110：common_end 时窗口长度不等，horizon 并不生效，不能谎报成固定 horizon
                         "horizon_sessions": horizon if end_mode == "fixed_length" else None,
                         "end_mode": end_mode,
                         "overlap_warning": "重叠窗口并非独立样本；不是样本外验证"
                                           + ("；common_end 下窗口长度不等，分位不可直接比较" if end_mode != "fixed_length" else "")}}


def holdout(manifest, bars, config, factory, params, test_start, gap_sessions=0, progress=None):
    if gap_sessions < 0:
        raise ValueError("隔离间隔不得为负")
    bars = prepare_bars(bars)
    days = [d for d in manifest["sessions"] if config.start <= d <= config.end]
    if test_start not in days:
        raise ValueError("留出起点必须是交易日")
    i = days.index(test_start)
    train_end = i - gap_sessions - 1
    if train_end < 1 or len(days) - i < 2:
        raise ValueError("开发/留出区间不足")
    outputs = {}
    plans = (("development", config.start, days[train_end]), ("holdout", test_start, config.end))
    passes_per_window = 2 if config.benchmark else 1
    total_work = sum(sum(start <= day <= end for day in days) * passes_per_window for _, start, end in plans)
    completed_work = 0
    for label, start, end in plans:
        subset = replace(config, start=start, end=end, cash_flows={d: v for d, v in config.cash_flows.items() if d == "monthly" or start <= d <= end})
        session_count = sum(start <= day <= end for day in days)
        offset = completed_work
        run_progress = (lambda done, _total, base=offset: progress(base + done, total_work)) if progress else None
        result = simulate(manifest, bars, subset, factory(), deepcopy(params), run_progress)
        completed_work += session_count
        benchmark_offset = completed_work
        benchmark_progress = (lambda done, _total, base=benchmark_offset: progress(base + done, total_work)) if progress and config.benchmark else None
        benchmark = benchmark_result(manifest, bars, subset, benchmark_progress)
        if benchmark is not None:
            # C-115：每个留出段也给出相对基准，便于逐段对照
            result.setdefault("metrics", {})["relative_return"] = (
                result["metrics"]["total_return"] - benchmark["metrics"]["total_return"])
        if config.benchmark:
            completed_work += session_count
        outputs[label] = {"start": start, "end": end, "result": result, "benchmark": benchmark}
        if progress:
            progress(completed_work, total_work)
    trust = _trust_summary([{"status": "completed", "result": item["result"]} for item in outputs.values()])
    return {"kind": "holdout", **outputs,
            "metadata": {**research_metadata(config), **trust, "test_start": test_start, "gap_sessions": gap_sessions,
            "initialization": "独立空仓账户与策略状态，仅允许历史预热", "parameter_selection": "无自动参数搜索；冻结代码和参数后运行"}}
