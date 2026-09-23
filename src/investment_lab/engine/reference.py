"""Explicit price-only research on the existing Yahoo reference snapshots."""

REFERENCE_MODE = "reference_research"
REFERENCE_BASIS = "split_adjusted_reference_only"
YAHOO_BLOCK = "Yahoo 历史价格为拆股调整参考序列，尚未还原真实交易价；仅可查阅"
REFERENCE_WARNING = (
    "参考历史价格研究：按下一交易日的参考收盘价加滑点模拟，价格已按拆股调整，"
    "不代表当时实际成交价格。忽略拆股和现金分红事件，收益不含股息；"
    "数量按1个参考份额取整，供应商成交量仅作假设性参与率限制。"
    "挂牌边界、历史每手数和成交量口径未认证；仅在已有数据区间研究。"
    "事后调整因子可能含未来公司行动信息，不属于严格时点回测，也不进入正常排名。"
)


def reference_available(manifest, symbols=None):
    securities = manifest.get("securities", {})
    selected = list(securities) if symbols is None else symbols
    if manifest.get("source", {}).get("provider") != "Yahoo/chart" or not selected:
        return False
    return all(
        s in securities
        and securities[s].get("kind") in ("stock", "etf", "leveraged_etf", "index")
        and not securities[s].get("inverse")
        and securities[s].get("execution_blocked") in (None, "", YAHOO_BLOCK)
        for s in selected
    ) and any(securities[s].get("kind") != "index" for s in selected)
