"""涨跌停价推算（2026-10-26，用户裁决"按我的方案"）。

引擎里原来有一处守卫：买单遇到 `bar["limit_up"]` 且当日最高价触及就不成交。但全仓没有任何
代码产出这两个字段，等于守卫从未生效——涨停日照常按收盘价买入，回测收益系统性偏高。

这里按「前收盘价 × 板块幅度」现算，不写进行情数据（写进 bar 会改 manifest 摘要、进而改快照编号）。

口径（我的决定，用户已授权）：
  · 主板（60/00）10%，创业板/科创板（30/688）20%，北交所（4x/8x/92）30%；
  · 主板 ST 5%；创业板/科创板 ST 仍按 20%；北交所 ST 仍 30%；
  · 四舍五入到分（`ROUND_HALF_UP`）；
  · 算不出来（缺 preclose、ETF/基金/可转债/期货、未知板块）返回 None——调用方保持"按现价成交"，
    不假装有守卫。
"""
import re
from decimal import Decimal, ROUND_HALF_UP

from investment_lab.common import dec   # 与引擎其余部分共用 Decimal 口径

CENT = Decimal("0.01")


_CODE = re.compile(r"(\d{6})")


def _code(symbol) -> str:
    """从 sh.600519 / 600519.SH / 600519 里取出 6 位代码（真实快照带交易所前缀）。"""
    m = _CODE.search(str(symbol or ""))
    return m.group(1) if m else ""


def _is_st(value) -> bool:
    """BaoStock 的 isST 是字符串 "1"/"0"；bool("0") 为真，必须先归一。"""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "st")


def _ratio(symbol: str) -> Decimal | None:
    s = _code(symbol)
    if not s:
        return None
    if s.startswith(("688", "689", "300", "301")):
        return Decimal("0.20")
    if s.startswith(("43", "83", "87", "92")):
        return Decimal("0.30")
    if s.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return Decimal("0.10")
    return None


def limits_for(symbol: str, preclose, is_st) -> tuple[Decimal, Decimal] | None:
    """返回 (涨停价, 跌停价)；无法可靠推算时返回 None。"""
    ratio = _ratio(symbol)
    if ratio is None:
        return None
    try:
        base = dec(preclose)
    except Exception:  # noqa: BLE001 —— 空值/坏值都当作"算不出来"
        return None
    if base is None or base <= 0:
        return None
    if _is_st(is_st) and _code(symbol).startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        ratio = Decimal("0.05")
    up = (base * (1 + ratio)).quantize(CENT, rounding=ROUND_HALF_UP)
    down = (base * (1 - ratio)).quantize(CENT, rounding=ROUND_HALF_UP)
    return up, down


def limits_for_bar(security: dict, bar: dict,
                   preclose=None) -> tuple[Decimal, Decimal] | None:
    """只有股票才有涨跌停；ETF/基金/可转债/期货一律不推算。

    preclose 由调用方传（引擎用上一有效收盘兜底）；没有就取 bar 自己的。
    security 里显式的 `limit_ratio` 是给"符号不是 6 位代码"的测试/合成数据用的。
    """
    kind = (security or {}).get("kind")
    if kind not in (None, "stock"):
        return None
    symbol = ((security or {}).get("symbol") or bar.get("symbol")
              or (security or {}).get("code") or bar.get("code") or "")
    st = (security or {}).get("is_st", bar.get("is_st"))
    base = preclose if preclose is not None else bar.get("preclose")
    if not _code(symbol):
        ratio = (security or {}).get("limit_ratio")
        try:
            r = dec(ratio) if ratio not in (None, "") else None
        except Exception:  # noqa: BLE001
            r = None
        if r is None:
            return None
        try:
            b = dec(base)
        except Exception:  # noqa: BLE001
            return None
        if b is None or b <= 0:
            return None
        return ((b * (1 + r)).quantize(CENT, rounding=ROUND_HALF_UP),
                (b * (1 - r)).quantize(CENT, rounding=ROUND_HALF_UP))
    return limits_for(symbol, base, st)


def st_rule_report(main_board_checked, st_flag_seen):
    """C-161（修法 C）：把「ST 5% 规则到底有没有数据支撑」变成一句能被读的话。

    为什么需要：ST 判定要读 `is_st`，但**东财来源的 bar 与 open_acquire 构造的 security
    都没有这个字段**（只有 BaoStock 的 bar 有）。于是东财数据上的主板 ST 股按 10% 判涨跌停，
    而真实规则是 5%——保护静默失效。这不改变任何计算（口径修复另案评审），
    只是让"这条规则在这些数据上算不出来"**可见**。

    判决来自实际发生的尝试，不看数据源的自我声明：
      · not_used —— 本次没有主板股票尝试，谈不上这条规则；
      · verified —— 每次主板尝试都能读到 ST 标记（能区分 ST / 非 ST）；
      · partial  —— 一部分能读到、一部分读不到（混合来源），**不能给整批背书**；
      · unknown  —— 一次都没读到 ST 标记：ST 判定形同虚设。
    """
    checked, seen = int(main_board_checked or 0), int(st_flag_seen or 0)
    checked, seen = max(checked, 0), max(seen, 0)
    if checked == 0:
        status, note = "not_used", "本次没有主板股票成交尝试，谈不上 ST 涨跌停规则"
    elif seen == 0:
        status, note = "unknown", (
            "数据里没有 ST 标记：主板 ST 股按 10% 判涨跌停，而真实规则是 5%，"
            "这些成交的价限保护偏松（C-161）")
    elif seen < checked:
        status, note = "partial", (
            f"{seen}/{checked} 次主板尝试能读到 ST 标记，其余读不到——"
            "ST 规则只对一部分成交生效（C-161）")
    else:
        status, note = "verified", "每次主板尝试都能读到 ST 标记，ST 5% 规则有数据支撑"
    return {"status": status, "main_board_checked": checked, "st_flag_seen": seen, "note": note}


def guard_report(applied, skipped, source=None):
    """C-142（§14 第 1 条）：把「价限守卫有没有真的生效」变成一句能被读的话。

    判决来自**实际发生的订单-交易日尝试**，不是数据源的自我声明：
      · not_used     —— 本次没有任何成交尝试，谈不上守卫；
      · unavailable  —— 全部尝试都算不出涨跌停（缺 preclose / 非股票 / 未知板块），这些成交没有价限保护；
      · partial      —— 一部分算得出、一部分算不出；
      · applied      —— 全部尝试都有价限判定。
    """
    if isinstance(source, dict):
        provider = source.get("provider") or source.get("name") or source.get("source") or "unknown"
    else:
        provider = str(source or "unknown")
    applied, skipped = int(applied or 0), int(skipped or 0)
    total = applied + skipped
    if total == 0:
        status = "not_used"
        note = "本次没有订单-交易日尝试，谈不上价限守卫"
    elif applied == 0:
        status = "unavailable"
        note = "所有成交尝试都算不出涨跌停（缺 preclose / 非股票 / 未知板块），这些成交没有价限保护"
    elif skipped == 0:
        status = "applied"
        note = "全部成交尝试都有价限判定"
    else:
        status = "partial"
        note = f"{skipped}/{total} 次成交尝试算不出涨跌停，这部分没有价限保护"
    return {"status": status, "applied": applied, "skipped": skipped, "source": provider, "note": note}
