# Python 策略 API v1

文件放 `本机数据目录下的 `user_strategies` 文件夹`，可使用网页编辑。保存保留旧代码内容快照，回测独立保存全部项目内 .py 依赖。允许 Pandas、NumPy 和自写辅助模块；仅运行你信任的代码。

页面可勾选多个同市场、同币种的快照用于同一次回测。“交易标的”中选中的项目按显示顺序组成 `ctx.symbols`，共用同一个账户。Python策略需要遍历标的或显式设置各自权重才会交易多只股票；勾选本身不会自动买入。现有买入持有、定投、均线、回撤买入及杠杆再平衡内置示例只交易第一项；“同市场轮动”会在所选标的中比较。组合快照限制和冻结方法见ENGINE.md。

```python
import numpy as np

def initialize(ctx):
    ctx.state['entered'] = False

def on_session(ctx):
    symbol = ctx.symbols[0]
    prices = ctx.history(symbol, 20)  # 默认 VWAP，只含当前已知历史
    if len(prices) == 20 and all(p is not None for p in prices):
        if prices[-1] > sum(prices) / 20:
            ctx.order_target_weight(symbol, 0.9, '价格在历史均线之上')
        else:
            ctx.order_target_weight(symbol, 0, '退出已有多头')

def on_finish(result):
    print('成交笔数', len(result.trades))
```

- ctx.date / symbols / params / state：当日、当前池、本轮参数、窗口内独立状态。
- ctx.cash / debt / equity / positions / pending：账户和待处理订单的副本。
- ctx.history(symbol, count, field=None)：Decimal 历史序列；None 字段默认 VWAP（显式收盘研究模式为 close）。明确指定 field='close' 可读未复权收盘。
- ctx.bars(symbol, count)：历史完整字段副本。缺失指标值保留 None，策略应处理。
- ctx.order_shares(symbol, quantity, reason)：正数开多，负数仅卖出已有多头；期货数量为合约数。
- ctx.order_value(symbol, amount, reason)：按已知收盘换算数量，期货包含乘数。
- ctx.order_target_weight(symbol, weight, reason)：目标净值权重，允许研究融资权重 > 1，实际成交受账户约束。

initialize 在第一轮交易日收盘回调前运行，不能在期初持仓。on_finish 仅得到结果副本，不再下单。滚动每个窗口/留出两段重新实例化策略并固定随机种子。金额/权重下单的待处理队列以回调开始快照计算；同一回调请勿对同一标的重复提交互相覆盖的目标权重。

内置买入持有、定投、均线、回撤买入、轮动、杠杆再平衡及预先固定日期的期货换月均为 API 示例，不宣传其投资有效性。自写模型拟合必须只使用 history/bars 已知数据；本版不提供自动参数搜索、标签 purge 或受安全沙箱保护的数据访问。

CLI 请求例见 config/example_run.json。先从 `investment-lab list` 获取实际 snapshot ID；网页可直接选定。
