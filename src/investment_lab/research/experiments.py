from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date
import numpy as np

from investment_lab.engine.core import simulate
from investment_lab.engine.reference import REFERENCE_MODE, REFERENCE_WARNING
from investment_lab.engine.cash_flows import MONTHLY_RULE


def research_metadata(config):
    metadata = {"monthly_deposit_rule": MONTHLY_RULE} if "monthly" in config.cash_flows else {}
    if config.mode == REFERENCE_MODE:
        metadata.update(reference_only=True, normal_ranking_eligible=False,
                        research_warning=REFERENCE_WARNING, point_in_time_prices_verified=False)
    return metadata


def benchmark_result(manifest, bars, config, progress=None):
    if not config.benchmark:
        return None
    from investment_lab.strategies.examples import Builtin
    other = replace(config, symbols=[config.benchmark], benchmark=None, max_leverage="1")
    return simulate(manifest, bars, other, Builtin(config.benchmark_strategy), {"weight": "0.95"}, progress)


def rolling(manifest, bars, config, factory, params, interval="month", horizon=60, end_mode="fixed_length", progress=None):
    if interval not in ("day", "month", "quarter", "year") or end_mode not in ("fixed_length", "common_end") or horizon < 2:
        raise ValueError("滚动参数无效")
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
            sample = {"start": start, "end": days[end_i], "status": "completed", "metrics": result["metrics"],
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
    if not values:
        raise ValueError("全部滚动窗口被跳过: " + "; ".join(s.get("reason", "") for s in samples[:4]))
    return {"kind": "rolling", "samples": samples, "summary": {"count": len(values), "skipped": len(samples) - len(values),
            "loss_ratio": sum(v < 0 for v in values) / len(values), "median": float(np.median(values)),
            "q10": float(np.quantile(values, .1)), "q90": float(np.quantile(values, .9)), "best": max(values), "worst": min(values)},
            "metadata": {**research_metadata(config), "interval": interval, "horizon_sessions": horizon, "end_mode": end_mode, "overlap_warning": "重叠窗口并非独立样本；不是样本外验证"}}


def holdout(manifest, bars, config, factory, params, test_start, gap_sessions=0, progress=None):
    if gap_sessions < 0:
        raise ValueError("隔离间隔不得为负")
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
        if config.benchmark:
            completed_work += session_count
        outputs[label] = {"start": start, "end": end, "result": result, "benchmark": benchmark}
        if progress:
            progress(completed_work, total_work)
    return {"kind": "holdout", **outputs, "metadata": {**research_metadata(config), "test_start": test_start, "gap_sessions": gap_sessions,
            "initialization": "独立空仓账户与策略状态，仅允许历史预热", "parameter_selection": "无自动参数搜索；冻结代码和参数后运行"}}
