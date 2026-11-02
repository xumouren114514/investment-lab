r"""T-038：`sell_tax_bps=0` 对**高频策略**的影响可达年化 12.5%，不只是"轻微高估"。

## 上一轮的量级估计错了一半

T-036 我只写了"含卖出的回测收益会被高估"，没有量化。
这一轮算了一下，发现**影响完全取决于换手率**：

| 场景 | 印花税对本金的拖累 |
|---|---|
| 买入持有一年卖出（1 次换手） | 0.05 个百分点 |
| 一年 12 次换手 | 0.60% |
| 一年 50 次换手 | 2.50% |
| **一年 250 次换手（日频）** | **12.50%** |

A 股印花税是单边 0.05%（卖出时收）。

## 为什么这比"注释没写"严重

`commission_bps="3"` 至少不是 0——它有成本。而 `sell_tax_bps="0"` 意味着：

- 低频策略：影响小，可以接受（但应写明）
- **高频策略：一年吃掉 12.5% 的收益，回测看起来能赚钱的策略实际会亏**

而且 `models.py` 的默认 `mode="strict_vwap"` 说明这是个**严肃的回测引擎**，
不是玩具。它的默认值偏乐观，会系统性地高估所有含卖出的结果。

## 判据

1. 默认参数组合必须能算出"最坏情况下的成本拖累"，并写进文档；
2. 这个数字要**可复算**（给公式，不只是一个结论）；
3. 反向：把 sell_tax 改成 0 或改走样，判据要能抓。
"""
import re
import sys
from pathlib import Path

LAB = Path(r"D:\deepseek\investment_lab")
DOCS = LAB / "docs"
MODELS = LAB / "src" / "investment_lab" / "engine" / "models.py"

# A 股卖出印花税：单边 5 bps
STAMP_BPS = 5


def _doc_text() -> str:
    parts = []
    for p in DOCS.rglob("*.md"):
        try:
            parts.append(p.read_bytes().decode("utf-8", "replace"))
        except OSError:
            continue
    return "\n".join(parts)


def calc_drag_bps(turns: int, notional_bps: int = STAMP_BPS) -> float:
    """一年 turns 次完整换手，印花税对本金的拖累（百分比）。"""
    return turns * notional_bps / 100     # bps/100 = %


def test_impact_is_quantified_somewhere():
    """印花税的影响必须在文档里有**数字**，不能只有"会被高估"这种定性说法。"""
    text = _doc_text()
    # 找含 sell_tax/印花税 的段落里有没有数字 + %
    found = []
    for line in text.splitlines():
        if ("印花税" in line or "sell_tax" in line) and re.search(r"\d+(\.\d+)?\s*%", line):
            found.append(line.strip()[:100])
    assert found, (
        "文档里找不到印花税影响的具体数字。\n"
        "T-036 只写了「回测收益会被高估」——那是定性说法，读者无法判断严重程度。\n"
        "至少应给出：低频（1 次/年）≈0.05 个百分点、日频（250 次/年）≈12.5%。"
    )
    print(f"（信息）找到的量化说明：{found[:2]}")


def test_calculation_is_reproducible():
    """量化说明必须给出可复算的方式（公式或换算），不能只给结论数字。"""
    text = _doc_text()
    hit = [l for l in text.splitlines() if ("印花税" in l or "sell_tax" in l)
           and ("换手" in l or "bps" in l or "×" in l or "*" in l or "5 bps" in l)]
    assert hit, (
        "文档里没有给出印花税拖累的**换算方式**（如「每次换手 × 5bps」）。\n"
        "只给结论数字的话，读者没法自己验证。"
    )


def test_default_sell_tax_is_zero_and_documented():
    """默认 0 必须有醒目说明——这是"偏乐观假设"，不是"没有成本"。"""
    src = MODELS.read_bytes().decode("utf-8")
    if 'sell_tax_bps: str = "0"' not in src:
        print("（信息）默认值已不是 0，这条不再适用")
        return
    # models.py 里那一行附近必须有"高估"或"假设"
    for line in src.splitlines():
        if 'sell_tax_bps: str = "0"' in line:
            assert ("假设" in line or "高估" in line), (
                f"models.py 里 sell_tax_bps=0 那行没有警示：{line.strip()[:100]}"
            )
            print(f"（信息）{line.strip()[:100]}")
            return
    raise AssertionError("找不到 sell_tax_bps 那行")


def test_math():
    """判据自己的算式要对（1 次 = 0.05%，250 次 = 12.5%）。"""
    assert abs(calc_drag_bps(1) - 0.05) < 1e-9, calc_drag_bps(1)
    assert abs(calc_drag_bps(250) - 12.5) < 1e-9, calc_drag_bps(250)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:  # noqa: BLE001
        pass
    fails = 0
    ran = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            ran += 1
            try:
                fn(); print("PASS", name)
            except AssertionError as e:
                fails += 1; print("FAIL", name, e)
    print(f"{fails} 条失败（共 {ran} 条）" if fails else f"全部通过（{ran} 条）")
    sys.exit(1 if fails else 0)
