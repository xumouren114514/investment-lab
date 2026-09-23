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
