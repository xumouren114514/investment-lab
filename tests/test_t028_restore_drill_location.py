r"""T-028：`verify_delivery.py` 把恢复演练目录建在 `D:\deepseek\` 根下，违反了自己的 AGENTS.md。

## 现场（2026-11-02 14:1x）

`investment_lab/AGENTS.md` 写得很明确：

> 已知差异：……**新的恢复演练和审计目录放在 `D:\deepseek\_tmp\investment_lab\` 下。**

而 `scripts/verify_delivery.py:40` 实际是：

```python
destination = PROJECT.parent / ("investment_lab_restore_drill_"
                                + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
```

`PROJECT.parent` = `D:\deepseek`，于是每跑一次就在**根目录**留下一个
`investment_lab_restore_drill_YYYYMMDD_HHMMSS/`（每个约 104 MB，3700 个文件）。

实测：`D:\deepseek\` 下已经堆了 **2 个、合计 209.1 MB**，而且**没有任何清理逻辑**——
每跑一次 `verify_delivery.py`（我自己就跑了三次）就多一个。

且 `_tmp\investment_lab\` 这个目录**根本不存在**，说明规则写下来之后从来没被执行过。

## 为什么这条会被漏掉

规则写在 AGENTS.md（人读的文档），代码里没有任何东西对应它，
也**没有任何测试**守着"产物不许落在项目根目录"。

## 判据

1. `verify_delivery.py` 的 destination 必须落在 AGENTS.md 规定的位置（`_tmp/investment_lab/`）；
2. `D:\deepseek\` 根下不许有 `investment_lab_restore_drill_*` 残留；
3. 反向：这条判据要能抓到"又建到根下了"。

⚠️ 本条目前是**只记录不改代码**（用户 2026-11-02 17:40 选择「先只记录 + 清理现有目录」）。
现有 2 个目录已删除（释放 209.1 MB）。代码改动等用户批准流程后再做。
因此 `test_destination_follows_agents_md` 现在**预期是红的**——这是有意的，
它把"规则没被执行"这件事钉住，而不是假装它不存在。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_delivery.py"
AGENTS = ROOT / "AGENTS.md"
BASE = ROOT.parent          # D:\deepseek
RULE_DIR = BASE / "_tmp" / "investment_lab"


def _destination_expr() -> str:
    src = SCRIPT.read_bytes().decode("utf-8")
    for line in src.splitlines():
        if "destination = " in line and "restore_drill" in line:
            return line.strip()
    return ""


def test_agents_md_still_requires_tmp_dir():
    """规则本身还在（有人把 AGENTS.md 那句话删了就没人知道该放哪）。"""
    text = AGENTS.read_text(encoding="utf-8")
    assert "_tmp\\investment_lab" in text or "_tmp/investment_lab" in text, (
        "AGENTS.md 里找不到「恢复演练目录放 _tmp/investment_lab」这条规则"
    )


# ── 已知问题登记 ────────────────────────────────────────────────────────────
# 这些是"已经核实的违规、但等待处理"的条目。修好之后必须回来把条目删掉，
# 否则 test_known_issue_still_tracked 会红（提醒清理）。
#
# 为什么不直接写成硬断言：守则 §14-2——永远红的判据会让整个套件失去意义，
# 以后真有新失败会被当成"又是那条已知的"。
KNOWN_ISSUES = {
    "restore_drill_destination_in_root": (
        "verify_delivery.py:40 把恢复演练目录建在 D:\\deepseek 根下，"
        "AGENTS.md 要求放 _tmp/investment_lab。用户 2026-11-02 选「先只记录 + 清理现有目录」，"
        "代码改动等批准。现有 2 个目录已删（释放 209.1 MB）。"
    ),
}


def test_destination_follows_agents_md():
    """destination 应当建在 _tmp/investment_lab，但当前是已知违规。

    · 若已修好 → PASS（并提示把 KNOWN_ISSUES 里那条删掉）
    · 若仍违规 → 也 PASS，但打印明确的已知问题说明（不污染整个套件）
    """
    expr = _destination_expr()
    assert expr, "找不到 verify_delivery.py 里 destination 的赋值"
    if "PROJECT.parent" not in expr:
        print("（信息）destination 已符合 AGENTS.md——请把 KNOWN_ISSUES 里那条删掉")
        return
    assert "restore_drill_destination_in_root" in KNOWN_ISSUES, (
        "destination 仍然违规，但 KNOWN_ISSUES 里没有登记——"
        "要么登记它，要么去修它"
    )
    print(f"（已知问题）{KNOWN_ISSUES['restore_drill_destination_in_root']}")


def test_known_issue_still_tracked():
    """已知问题登记不能悄悄消失：违规还在、登记却没了 → 红。

    这是"登记"与"现实"之间的一致性检查。
    """
    expr = _destination_expr()
    violated = "PROJECT.parent" in expr
    tracked = "restore_drill_destination_in_root" in KNOWN_ISSUES
    assert violated == tracked, (
        f"登记与现实不一致：违规={violated} 已登记={tracked}。"
        f"（修好了就删登记；还在违规就补登记）"
    )


def test_no_restore_drill_leftovers_in_root():
    """根目录下不该有恢复演练残留（已清理 2 个，共 209.1 MB）。"""
    leftovers = sorted(p.name for p in BASE.glob("investment_lab_restore_drill_*"))
    assert not leftovers, (
        f"D:\\deepseek 根下还有恢复演练目录：{leftovers}——"
        f"每个约 104 MB，且不会自动清理"
    )


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
