# 内置投资策略与 Python 策略 API

本机版和 GitHub Pages 浏览器版使用同一份内置策略目录：[catalog.json](../src/investment_lab/strategies/catalog.json)。目录定义名称、说明、适用标的范围、参数类型、默认值、范围和填写提示；本机 `/api/strategies` 返回这份目录，Pages 构建也从该文件生成目录。修改内置程序时同步更新目录和策略测试。

内置程序依据决策日及此前已知的行情产生规则信号，不是对未来收益的预测模型。策略信号在当前日收盘后确定，订单由回测引擎按下一交易日模拟执行。指标窗口未满或所需 OHLC 字段缺失时跳过信号；不会用别的价格字段补足。所选成交价格模式、费用、交易规则和数据质量会影响结果；历史测试不代表未来表现或实际成交。

## 内置策略

每个策略的表单默认值与可填写范围以共享目录为准。网页的高级 JSON 字段可保留目录以外的旧参数；保存的配置同时记住各策略各自的参数。

| 分类 | 策略 ID | 用途 |
|---|---|---|
| 持有与定投 | `buy_hold` | 首次建立目标仓位后持有。 |
| 持有与定投 | `dca` | 按周、月、季或年投入固定金额。 |
| 持有与定投 | `adaptive_dca` | 定期定投；历史回撤达到阈值时按倍数增加当期投入。 |
| 组合配置 | `monthly_equal_weight` | 动态等权；低配优先补仓，按现有 5 个百分点阈值检查再平衡，可选指定检查日期或不再平衡。 |
| 组合配置 | `target_allocation` | 设置所选标的目标比例及再平衡频率；未配置的比例留在现金中。 |
| 趋势与动量 | `moving_average` | 按当前价格相对简单均线的关系设置仓位。 |
| 趋势与动量 | `ema_crossover` | 短、长 EMA 发生交叉时入场或退出。 |
| 趋势与动量 | `macd` | MACD 线与信号线交叉时入场或退出。 |
| 趋势与动量 | `time_series_momentum` | 比较回看区间首尾价格，按时间序列动量设置仓位。 |
| 趋势与动量 | `rotation` | 在多项所选标的中按过去动量定期轮动。 |
| 突破与回撤 | `donchian_breakout` | 按此前高低价通道突破入场、跌破退出。 |
| 突破与回撤 | `drawdown_buy` | 历史回撤达到阈值时建立目标仓位。 |
| 突破与回撤 | `drawdown_ladder` | 回撤逐级加深时按金额分层投入。 |
| 均值回归 | `rsi_mean_reversion` | RSI 到达超卖线时入场，回升至退出线时退出。 |
| 均值回归 | `bollinger_mean_reversion` | 收盘低于布林下轨时入场，回到中轨时退出。 |
| 均值回归 | `stochastic_mean_reversion` | 随机指标到达超卖线时入场，回升至退出线时退出。 |
| 风险控制 | `atr_trend_stop` | 趋势线上方入场，按 ATR 止损距离估算风险仓位，并以收盘价跟踪止损。风险比例是研究假设，不保证最大亏损。 |
| 风险控制 | `volatility_target` | 按历史实现波动率缩放仓位，并受最大仓位约束。 |
| 组合配置 | `inverse_volatility` | 定期按各标的历史波动率倒数配置仓位；单项上限后的余额保留为现金。 |
| 风险与研究对照 | `leverage_rebalance` | 每日尝试恢复目标杠杆仓位；实际订单仍受账户规则约束。 |
| 风险与研究对照 | `futures_roll` | 按用户预先填写的日期映射换月，不用未来成交量选择合约。 |
| 风险与研究对照 | `cash` | 不下单，用于核对资金流与区间结果。 |

当前引擎不具备完整基本面历史、配对做空、期权定价和盘中逐笔数据能力，因此目录不把这些策略标作可验证的内置策略。期货换月和持有现金是研究/对照工具。

## Python 策略 API v2（本机版）

自定义 `.py` 文件放在本机数据目录的 `user_strategies` 文件夹，可使用网页编辑。保存会保留旧代码快照；提交回测时冻结 Python 源码、参数、引擎版本和数据快照。只运行自己信任的代码。本机 API 只监听回环地址，不应直接暴露至公网。GitHub Pages 只运行上表中的固定内置策略，不执行访客上传的任意 Python 代码。

```python
def initialize(ctx):
    ctx.state['entered'] = False

def on_session(ctx):
    symbol = ctx.symbols[0]
    prices = ctx.history(symbol, 20)  # 默认字段随价格研究模式；只含当前已知历史
    if len(prices) != 20 or any(value is None for value in prices):
        return
    average = sum(prices) / len(prices)
    ctx.order_target_weight(
        symbol,
        0.9 if prices[-1] > average else 0,
        '按已知价格相对均线设置仓位',
    )
```

- `ctx.date / symbols / params / state`：当日、所选标的、本轮参数、窗口内状态。
- `ctx.cash / debt / equity / positions / pending`：账户和待处理订单的当前副本。
- `ctx.history(symbol, count, field=None)`：Decimal 历史序列；未指定字段时使用成交模式对应字段。`field='close'` 可明确读取未复权收盘价。
- `ctx.bars(symbol, count)`：所请求的完整历史字段副本；缺失值保留为 `None`。
- `ctx.order_shares(symbol, quantity, reason)`：正数开多、负数卖出现有多头；期货数量为合约数。
- `ctx.order_value(symbol, amount, reason)`：按引擎可用价格换算下单数量。
- `ctx.order_target_weight(symbol, weight, reason)`：目标净值权重，可使用大于 1 的融资权重；实际成交受账户限制。

`initialize` 在首个研究交易日的策略回调前运行，不能设置期初持仓。`on_finish(result)` 只得到结果副本，不再下单。滚动和留出研究会按窗口重新初始化策略并固定随机种子。每轮同一标的避免重复提交互相覆盖的目标订单。

策略只用 `history` / `bars` 返回的已知数据计算；不要从全量行情文件或未来日期取数。交易日历、公司行动、VWAP 和真实券商规则仍需按数据来源逐项核验。浏览器版与本机版的严格 VWAP、参考价格研究等数据口径边界相同。

CLI 请求例见 `config/example_run.json`。先从 `investment-lab list` 获取实际快照 ID；网页可直接选择数据快照。
