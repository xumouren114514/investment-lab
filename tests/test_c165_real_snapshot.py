r"""C-165：用**真实行情快照**验证 C-161（ST 可见性）与 C-163（除息日前收）。

为什么需要这一条：C-161 与 C-163 都是在**合成数据**上验证的（`simulate` + 手搭 bars）。
守则 §5 说"真实产物优先于自造样本"、§13-3 说"保护必须在真实数据形态上验证"——
这个项目已经踩过"价限守卫在真实数据上完全没生效"的坑（符号是 `sh.600519`、
preclose 只有 BaoStock 有、is_st 是字符串 `"0"`）。

这个脚本直接读 `investment_lab_data/manifests` 里**真实入库的** BaoStock 快照，
确认三件事：
  1. 真实 bar 确实带 `preclose` 与 `is_st`（且 `is_st` 是**字符串**）；
  2. 价限守卫在真实数据上确实算得出（不是静默跳过）；
  3. `st_rule_report` 在"有标记 / 无标记 / 部分有标记"三种形态下给出正确判决。

跳过策略：找不到 CN 股票的真实快照时 skip（不假装通过）。

跑法：.venv\Scripts\python -m pytest tests\test_c165_real_snapshot.py -q
"""
import gzip
import json
from pathlib import Path

import pytest

DATA_ROOT = Path(r"D:\deepseek\investment_lab_data")
MANIFESTS = DATA_ROOT / "manifests"
MARKET = DATA_ROOT / "market"

pytestmark = pytest.mark.skipif(
    not MANIFESTS.is_dir(),
    reason="真实数据目录不存在（这是本机私有的，CI 上应当 skip 而不是假绿）",
)


def _first_cn_manifest():
    """找第一份带 CN 股票的真实快照。"""
    if not MANIFESTS.is_dir():
        return None
    for f in sorted(MANIFESTS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        secs = m.get("securities") or {}
        cn = {k: v for k, v in secs.items()
              if (v or {}).get("market") == "CN" and (v or {}).get("kind") == "stock"}
        if cn:
            return m, cn
    return None


def _load_any_bars(manifest, symbol: str) -> list[dict]:
    """从 partitions 里读出**指定标的**的真实行情（任一有数据的年份）。

    第一版没按 symbol 过滤，于是 security 取的是 A 股票、bars 却可能来自 B 股票——
    测试报出「preclose=171.04 涨成 205.25」这种看不懂的数字，其实是两只票的数据串了。
    """
    for part in reversed(manifest.get("partitions") or []):
        if part.get("symbol") != symbol:
            continue
        fp = MARKET / (part["hash"] + ".json.gz")
        if not fp.is_file():
            continue
        data = json.loads(gzip.decompress(fp.read_bytes()).decode("utf-8"))
        bars = data if isinstance(data, list) else data.get("bars") or []
        good = [b for b in bars if b.get("symbol") == symbol]
        if good:
            return good
    return []


@pytest.fixture(scope="module")
def real_case():
    found = _first_cn_manifest()
    if found is None:
        pytest.skip("真实数据里没有 CN 股票的快照")
    manifest, cn = found
    symbol, security = next(iter(cn.items()))
    bars = _load_any_bars(manifest, symbol)
    if not bars:
        pytest.skip(f"{symbol} 的分区文件都不在本机")
    return symbol, security, bars


def test_real_bars_carry_preclose_and_is_st(real_case):
    """真实数据形态：preclose 与 is_st 都得在，且 is_st 是字符串。"""
    symbol, _security, bars = real_case
    with_preclose = sum(1 for b in bars if b.get("preclose") not in (None, "", 0))
    with_is_st = sum(1 for b in bars if "is_st" in b)
    assert with_preclose == len(bars), f"{symbol}: 只有 {with_preclose}/{len(bars)} 根带 preclose"
    assert with_is_st == len(bars), f"{symbol}: 只有 {with_is_st}/{len(bars)} 根带 is_st"
    sample = bars[0]["is_st"]
    assert isinstance(sample, str), (
        f"is_st 期望是字符串（BaoStock 的 \"0\"/\"1\"），实际是 {type(sample).__name__}；"
        "这个形态决定 price_limit._is_st 的归一化逻辑"
    )


def test_limit_guard_computes_on_every_real_bar(real_case):
    """价限守卫在真实数据上必须**每一根都算得出**，不能静默跳过。"""
    from investment_lab.engine.price_limit import limits_for_bar

    symbol, security, bars = real_case
    computed = 0
    for b in bars:
        if limits_for_bar(security, b, b.get("preclose")) is not None:
            computed += 1
    assert computed == len(bars), (
        f"{symbol}: 只有 {computed}/{len(bars)} 根算出了涨跌停——"
        "真实数据上算不出来等于守卫没生效（§13-3 踩过这个坑）"
    )


def test_real_bar_values_are_actually_correct(real_case):
    """**算得出不等于算得对**：逐根核对涨停价 = preclose × (1 + 应有幅度)。

    这条是 2026-10-30 补的：第一版只断言"每根都算得出"，于是把
    `_is_st` 的字符串归一化改成 `bool(value)`（真实数据的经典坑：`bool("0")` 为真）
    之后测试仍然全绿——因为被选中的那支标的 `is_st='0'` 被判成 ST，涨停价从 10% 变 5%，
    而"算得出"这件事没变。**断言必须落在数值上。**

    幅度按该 bar 自己的 `is_st` 定，但**判定不能被 ST 规则本身**：
    第一版用 `_is_st(b['is_st'])` 算期望幅度，于是把 `_is_st` 改坏之后，
    "被测对象"和"期望值"一起坏，测试照样通过——**用被测对象验证被测对象**。
    这里改成直接比较原始字符串：只有 `"1"` 才算 ST。
    """
    from decimal import Decimal, ROUND_HALF_UP

    from investment_lab.engine.price_limit import limits_for_bar

    symbol, security, bars = real_case
    st_values = {b.get("is_st") for b in bars}
    assert all(isinstance(v, str) for v in st_values), (
        f"{symbol} 的真实 is_st 取值集合 {st_values} 里有非字符串，归一化逻辑可能被绕过"
    )

    for b in bars[:50]:   # 取前 50 根足够，避免慢
        preclose = Decimal(str(b["preclose"]))
        # 只用原始字符串判断，不调 _is_st（否则期望值会被同一个 bug 带偏）
        ratio = Decimal("0.05") if str(b.get("is_st")).strip() == "1" else Decimal("0.10")
        expected_up = (preclose * (1 + ratio)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        got = limits_for_bar(security, b, preclose)
        assert got is not None, f"{symbol} {b['date']} 算不出涨跌停"
        assert got[0] == expected_up, (
            f"{symbol} {b['date']}: preclose={preclose}、is_st={b.get('is_st')!r} "
            f"→ 涨停算成 {got[0]}，按 {ratio:.0%} 应为 {expected_up}——"
            "is_st 的字符串归一化或板块幅度可能坏了"
        )


def test_is_st_normalization_rejects_string_zero():
    """`_is_st("0")` 必须是 False——`bool("0")` 为真是这个项目踩过的坑。"""
    from investment_lab.engine.price_limit import _is_st

    assert _is_st("0") is False, "_is_st(\"0\") 返回了真——bool(\"0\") 是 True，必须先归一化"
    assert _is_st("1") is True
    assert _is_st(False) is False
    assert _is_st(None) is False


def test_st_report_is_verified_on_real_bao_stock(real_case):
    """真实 BaoStock 数据上，ST 规则应当是 verified（有数据支撑）。"""
    from investment_lab.engine.price_limit import st_rule_report

    _symbol, _security, bars = real_case
    checked = len(bars)
    seen = sum(1 for b in bars if b.get("is_st") not in (None, ""))
    report = st_rule_report(checked, seen)
    assert report["status"] == "verified", (
        f"真实 BaoStock 数据应报 verified，实际 {report['status']}：{report['note']}"
    )


def test_st_report_states_unknown_without_flag():
    """对照：东财形态（无 is_st）必须报 unknown，不能假装有保护。"""
    from investment_lab.engine.price_limit import st_rule_report

    report = st_rule_report(174, 0)
    assert report["status"] == "unknown"
    assert "5%" in report["note"], "note 应当点明真实规则是 5%"


def test_st_report_states_partial_for_mixed_sources():
    from investment_lab.engine.price_limit import st_rule_report

    report = st_rule_report(174, 40)
    assert report["status"] == "partial"
    assert "40" in report["note"] and "174" in report["note"]


def test_st_price_limit_differs_by_data_shape():
    """核心对照：ST 股在两种数据形态下的涨停价不同，这正是 C-161 要暴露的事。"""
    from decimal import Decimal

    from investment_lab.engine.price_limit import limits_for_bar

    security = {"market": "CN", "kind": "stock", "exchange": "XSHG", "universe_id": "600001"}
    bar_no_flag = {"symbol": "sh.600001", "open": "10", "high": "10.50", "low": "10", "close": "10.50"}
    bar_flag = dict(bar_no_flag, is_st="1")

    up_no_flag = limits_for_bar(security, bar_no_flag, Decimal("10.00"))[0]
    up_flag = limits_for_bar(security, bar_flag, Decimal("10.00"))[0]

    assert up_flag == Decimal("10.50"), "带 is_st 时应当按 5% 算：10.00 × 1.05 = 10.50"
    assert up_no_flag == Decimal("11.00"), "没有 is_st 时只能按 10% 算：10.00 × 1.10 = 11.00"
    # 这一天的 high 正好是真实涨停价 10.50：
    assert Decimal("10.50") >= up_flag, "带标记时 10.50 触及涨停 → 应当不成交（正确）"
    assert not (Decimal("10.50") >= up_no_flag), "没有标记时 10.50 判未触及 → 放行（这就是风险）"
