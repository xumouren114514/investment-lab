from datetime import date, datetime
from investment_lab.common import dec


def vwap(amount=None, volume=None, amount_unit=1, volume_unit=1, same_scope=False):
    if not same_scope or amount is None or volume is None or dec(volume) <= 0:
        return None
    if dec(amount_unit) <= 0 or dec(volume_unit) <= 0 or dec(amount) <= 0:
        raise ValueError("成交额和单位必须为正")
    return str(dec(amount) * dec(amount_unit) / (dec(volume) * dec(volume_unit)))


def validate_dataset(securities, bars, actions, sessions, synthetic):
    errors, seen = [], set()
    for session in sessions:
        date.fromisoformat(session)
    for bar in bars:
        try:
            symbol, day = bar["symbol"], bar["date"]
            date.fromisoformat(day)
            key = (symbol, day)
            if key in seen:
                raise ValueError("重复证券/日期")
            seen.add(key)
            sec = securities[symbol]
            if sec.get("listed") and day < sec["listed"]:
                raise ValueError("上市前行情")
            if sec.get("delisted") and day > sec["delisted"]:
                raise ValueError("退市后行情")
            lo, hi = dec(bar["low"]), dec(bar["high"])
            if lo <= 0 or hi < lo or any(not lo <= dec(bar[k]) <= hi for k in ("open", "close")):
                raise ValueError("OHLC 不一致")
            if dec(bar["volume"]) < 0:
                raise ValueError("成交量为负")
            available = datetime.fromisoformat(bar["available_at"])
            if available.tzinfo is None:
                raise ValueError("可用时间必须含时区")
            value = bar.get("vwap_value")
            if value is not None:
                if dec(bar["volume"]) <= 0 or not lo <= dec(value) <= hi:
                    raise ValueError("VWAP 超出区间或零成交量")
                if not bar.get("vwap_method") or not bar.get("vwap_session"):
                    raise ValueError("VWAP 缺少方法/时段")
            if not synthetic and not bar.get("source"):
                raise ValueError("缺少真实数据来源")
            if sessions and day not in sessions:
                raise ValueError("行情不在交易日历")
        except (KeyError, ValueError, ArithmeticError) as exc:
            errors.append(f"{bar.get('symbol')}/{bar.get('date')}: {exc}")
    for action in actions:
        if action.get("symbol") not in securities:
            errors.append("公司行动证券不在数据集中")
        try:
            date.fromisoformat(action["date"])
        except (ValueError, KeyError):
            errors.append("公司行动日期无效")
        if action.get("type") not in ("split", "dividend"):
            errors.append("尚不支持该公司行动，需明确转换后导入")
        if action.get("type") == "split" and dec(action.get("ratio", 0)) <= 0:
            errors.append("拆股比例必须为正")
        if action.get("type") == "dividend" and (dec(action.get("amount", -1)) < 0 or action.get("pay_date", "") < action.get("date", "")):
            errors.append("分红金额或支付日无效")
    return errors


def coverage(manifest, bars):
    result = []
    for symbol, sec in manifest["securities"].items():
        rows = sorted((b for b in bars if b["symbol"] == symbol), key=lambda b: b["date"])
        dates = {r["date"] for r in rows}
        expected = [d for d in manifest["sessions"] if (not sec.get("listed") or d >= sec["listed"]) and (not sec.get("delisted") or d <= sec["delisted"])]
        strict = [r for r in rows if r.get("vwap_value") and r.get("quality_status") == "verified" and r.get("vwap_method") in ("vendor_verified", "amount_volume_verified")]
        candidates = [r for r in rows if r.get("vwap_value") and r.get("quality_status") != "verified"]
        result.append({"symbol": symbol, "name": sec.get("name", symbol), "market": sec["market"], "currency": sec["currency"],
                       "listed": sec.get("listed"), "listing_verified": sec.get("listing_verified", False),
                       "first": rows[0]["date"] if rows else None, "last": rows[-1]["date"] if rows else None,
                       "rows": len(rows), "strict_vwap_rows": len(strict), "candidate_vwap_rows": len(candidates),
                       "suspended_rows": sum(r.get("status") == "suspended" for r in rows),
                       "execution_blocked": sec.get("execution_blocked"),
                       "cash_action_count": sum(a["symbol"] == symbol and a["type"] == "dividend" for a in manifest.get("actions", [])),
                       "missing_sessions": [d for d in expected if d not in dates],
                       "actions_verified": sec.get("actions_verified", False), "synthetic": manifest["synthetic"]})
    return result


def invariants(manifest, bars, jump_threshold=0.25, max_examples=20):
    """C-141（§14 第 5 条）：数据质量不变量 + 覆盖率。**纯观测，不改任何计算结果。**

    三条不变量，每条都报 checked / uncovered / violations：
      A. 停牌日（status=suspended）的成交量与成交额必须为 0；
      B. preclose 必须等于上一根 bar 的收盘价——**当天有公司行动时豁免**
         （除权参考价本来就不等于昨收，严格比对会制造假警报）；
      C. 单日价格跳变超过 jump_threshold（默认 25%）时，必须能由当天的公司行动解释，
         否则记为"无解释跳变"。

    `uncovered` 是"**没数据可查**"（比如这根 bar 没有 preclose 字段），不是"查了没问题"——
    这正是这条要防的事：没查过的东西不能看起来像检查过。
    """
    by_symbol = {}
    for bar in bars:
        by_symbol.setdefault(bar["symbol"], []).append(bar)
    for rows in by_symbol.values():
        rows.sort(key=lambda b: b["date"])
    actions = {(a.get("symbol"), a.get("date")): a for a in manifest.get("actions", [])}

    suspended = {"checked": 0, "uncovered": 0, "violations": []}
    preclose = {"checked": 0, "uncovered": 0, "violations": []}
    jumps = {"checked": 0, "uncovered": 0, "violations": [], "explained": 0}

    for bar in bars:
        status = bar.get("status")
        if status == "suspended":
            suspended["checked"] += 1
            volume = dec(bar.get("volume") or 0)
            amount = dec(bar.get("amount") or 0)
            if volume != 0 or amount != 0:
                suspended["violations"].append(
                    f"{bar['symbol']}/{bar['date']} 停牌但量/额非零（vol={volume}, amount={amount}）")
        elif status != "trading":
            suspended["uncovered"] += 1   # 没有 status 字段，无从判断当天是否停牌

    for symbol, rows in by_symbol.items():
        for prev, cur in zip(rows, rows[1:]):
            prev_close = dec(prev.get("close") or 0)
            if cur.get("preclose") is None:
                preclose["uncovered"] += 1
            else:
                preclose["checked"] += 1
                if (symbol, cur["date"]) in actions:
                    pass   # 除权日的参考价本来就不等于昨收
                elif prev_close > 0:
                    diff = abs(dec(cur["preclose"]) - prev_close) / prev_close
                    if diff > 1e-6:
                        preclose["violations"].append(
                            f"{symbol}/{cur['date']} preclose={cur['preclose']} ≠ 前收 {prev_close}（差 {diff:.4%}）")

            jumps["checked"] += 1
            if prev_close <= 0:
                continue
            change = dec(cur.get("close") or 0) / prev_close - 1
            if abs(change) <= dec(str(jump_threshold)):
                continue
            if (symbol, cur["date"]) in actions:
                jumps["explained"] += 1
            else:
                jumps["violations"].append(
                    f"{symbol}/{cur['date']} 单日 {change:+.1%} 且当天无公司行动")

    def block(item):
        total = item["checked"] + item["uncovered"]
        return {"checked": item["checked"], "uncovered": item["uncovered"],
                "coverage": round(item["checked"] / total, 4) if total else None,
                "violation_count": len(item["violations"]),
                "violations": item["violations"][:max_examples],
                **({"explained": item["explained"]} if "explained" in item else {})}

    blocks = {"suspended_zero_volume": block(suspended),
              "preclose_consistency": block(preclose),
              "unexplained_jumps": block(jumps)}
    total = sum(b["violation_count"] for b in blocks.values())
    return {**blocks, "summary": {"violations": total, "ok": total == 0}}
