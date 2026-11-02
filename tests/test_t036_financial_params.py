r"""T-036：金融口径的默认值**没有依据说明**——守则 §九 要求「不能靠猜」。

## 守则原文（§九 决策边界）

> 三类必须留证据链、不能靠猜：**金融口径**（如涨跌停幅度、手续费）、
> **硬件操作**（虚拟机、设备）、**数据删除**。能算的算，不能算的标「未验证」，不假装正确。

## 现场（2026-11-02 22:38）

`investment_lab/src/investment_lab/engine/models.py:14-21`：

```python
commission_bps: str = "3"          # 万分之三
minimum_commission: str = "0"
sell_tax_bps: str = "0"
slippage_bps: str = "5"
```

**四个数字，没有一个注明依据。**

`docs/ENGINE.md` / `SPEC.md` 提到了这些参数存在，也说"缺少历史规则时使用明确标注的假设"，
但**具体数值（3 / 0 / 0 / 5）从哪来的，没有任何记述**。

## 为什么这算问题

守则 §九 说的不是"参数要有值"，而是"**不能靠猜**"。

- `sell_tax_bps = "0"` 意味着**卖出不交印花税**。A 股实际是单边 0.05%（5 bps）。
  如果这是刻意的研究假设，应当写明；如果只是默认零，那所有含卖出的回测**收益都被高估**。
- `commission_bps = "3"` 对 A 股偏高（常见万分之 1.5~3），对港股/美股口径不同。
- `minimum_commission = "0"` 意味着不考虑最低 5 元——**小额单的成本被低估**。

我不知道这几个值是对的还是错的。**但"没写依据"本身是可以判定的。**

## 判据

1. 金融参数（佣金/税费/滑点等）的默认值，**必须就近有一行说明依据**，
   或者明确标注"研究假设，未验证真实费率"；
2. 反向：不能用一个空注释糊弄（必须说明**来源或假设性质**）。
"""
import re
import sys
from pathlib import Path

LAB = Path(r"D:\deepseek\investment_lab")
MODELS = LAB / "src" / "investment_lab" / "engine" / "models.py"

# 需要依据说明的金融参数
FIN_PARAMS = ["commission_bps", "minimum_commission", "sell_tax_bps", "slippage_bps"]


def _lines() -> list[str]:
    return MODELS.read_bytes().decode("utf-8").splitlines()


def test_financial_params_have_rationale():
    """金融参数**这一段整体**必须有依据说明。

    ★ 第一版看「本行 ±1 行有没有注释」——**假阳性通过了**：
      四个参数是连续写的，删掉其中一个的注释，它下一行仍然有注释 → 窗口命中。
      现在改成检查**整段金融参数 + 上方 5 行**里有没有依据字样。
    """
    lines = _lines()

    # 找出所有金融参数所在的行号
    idxs = [i for i, l in enumerate(lines)
            if re.search(r"^\s*(" + "|".join(FIN_PARAMS) + r")\s*[:=]", l)]
    assert idxs, "一个金融参数都没找到——文件结构变了？"

    # 取这一段的范围（从最早一个参数往上 5 行，到最后)
    lo = max(0, min(idxs) - 5)
    hi = max(idxs) + 1
    block = "\n".join(lines[lo:hi])

    has_rationale = bool(re.search(r"假设|依据|未验证|来源|校准|研究", block))
    assert has_rationale, (
        "金融参数这一段没有任何依据说明。\n"
        "守则 §九 要求金融口径「不能靠猜」——至少写清是真实费率还是研究假设。\n"
        "缺少依据的参数："
        + ", ".join(FIN_PARAMS)
    )


def test_sell_tax_default_is_explained():
    """`sell_tax_bps` 默认 0 必须被**解释**，不能只是被提到。

    ★ 第一版只查「文档里有没有出现 sell_tax/印花税」——**假阳性**：
      `ENGINE.md` 里有一句「建模佣金、最低佣金、税费、滑点…」是**列举参数名**，
      不等于解释了"默认 0 意味着什么"。放开让它 PASS 了。
      现在要求：同一句话里必须同时出现「0/零/假设/未验证」这类字眼。
    """
    models_text = MODELS.read_bytes().decode("utf-8")
    if 'sell_tax_bps: str = "0"' not in models_text:
        print("（信息）sell_tax_bps 默认值不是 0，跳过这条")
        return

    explained = []
    for p in LAB.rglob("*.md"):
        try:
            text = p.read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        for line in text.splitlines():
            if "sell_tax" in line or "印花税" in line or "卖出税" in line:
                # 必须在同一句里说明"默认 0 / 假设 / 未验证"
                if any(k in line for k in ("默认 0", "默认零", "假设", "未验证", "不计", "忽略")):
                    explained.append((p.name, line.strip()[:100]))
    assert explained, (
        "`sell_tax_bps` 默认 0（卖出不交税），但没有任何一处文档解释它。\n"
        "A 股实际是单边 0.05%。如果这是研究假设，必须写清；否则所有含卖出的回测收益都被高估。"
    )
    print(f"（信息）已解释的出处：{explained[:2]}")


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
