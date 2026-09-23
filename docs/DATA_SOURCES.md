# 数据源评估（2026-09-20）

2026-09-20 补充：用户选择先使用开源与公开资源。当前入口和逐标实测以 [OPEN_RESOURCES.md](OPEN_RESOURCES.md) 及 [OPEN_RESOURCE_COVERAGE.md](OPEN_RESOURCE_COVERAGE.md) 为准；下文保留首版订阅比较，价格为当次核查快照。无需先购买订阅即可继续数据评估。


结论：先不购买。当前没有经样本核验能覆盖三地、1980 年以来历史、严格 VWAP、完整公司行动的一站式组合。先完成代表样本口径核实，再决定订阅。

| 来源 | 文档/套餐信息 | 已实际验证 | 仍缺少的证据 |
|---|---|---|---|
| EODHD EOD | 官方页面：全球 EOD $19.99/月；EOD+Intraday $29.99/月；All-in-One $99.99/月，个人使用 | 公开 AAPL API：1980-12-12—2026-09-18，11,534 条 OHLCV，日历缺口 0；原始响应和标准化快照已存本机 | EOD 不提供成交额/VWAP；成交量调整口径、公司行动与身份映射未独立核实。无法用日线 OHLCV 准确推出 VWAP |
| Tushare Pro | A股 daily 文档提供未复权行情，vol 为手、amount 为千元；权限页标注 daily 120 积分起，港股等存在独立权限 | 已实现字段换算与缺失/单位离线测试；用户无账户，未下载真实样本 | 各标的真实起止日、成交范围一致性、停牌/分红/配股、港股 ETF 与期货单位。不能因有 amount/vol 就自动标为严格可用 |
| Massive 美股 | 官方聚合接口有可选 vw 字段，按合格成交构建；Developer $79/月、10年历史；Advanced $199/月、20+年历史 | 仅核验官方文档，尚未实现账户实测 | 逐标起点、盘前盘后与竞价范围、公司行动/调整口径、长期留存许可。文档深度不等于本机下载证明，不能填补至1980年的缺口 |

建议验证顺序：A 股先用 Tushare 选 1 只大型股和 1 只 ETF；港股先核实股票/ETF 成交金额单位及交易时段；美股评估 Massive 的 vw 是否与选择的成交时段一致。每类抽查普通日、除权日、零量/停牌日及极端交易日。需要供应商账号/试用权限后再扩充约90只股票。EODHD 可作为长期 OHLC/公司行动研究候选，但不是严格 VWAP 的完整解决方案。

费用仅为核验当日官方公开方案，不含交易所/专业用户/税费等可能附加条件。港股权限价格/周期需以账户实际开通页面为准，此处不作购买推荐。

## 使用权与留存

EODHD 当前官方条款允许个人非商业存储与分析，禁止再分发/供他人展示。订阅到期后永久留存、备份复制细节与未来多用户展示的许可仍需书面确认。Tushare 与 Massive 的本地长期留存、备份、终止后留存和未来展示均未完成合同级核实。本版仅本机访问，不公开分发下载数据。

## 逐标覆盖

docs/data_coverage.json 有 116 条初始标的记录（90股票+13ETF+13指数），逐项列明身份、费用/许可核实状态和字段起止。除已下载 AAPL 的 OHLCV 外，未下载字段起止均为 null，不用文档声称冒充实际覆盖。网页“数据覆盖”展示已有快照的实时缺口和 VWAP 可用比例。

## 官方来源

- EODHD 字段、历史与公开 demo：[EOD API](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes)
- EODHD 当日费用：[Pricing](https://eodhd.com/pricing)
- EODHD 个人存储/再分发：[Terms](https://eodhd.com/financial-apis/terms-conditions)
- Tushare 字段与停牌说明：[daily](https://tushare.pro/document/2?doc_id=27)
- Tushare 权限：[积分及独立权限](https://tushare.pro/document/1?doc_id=108)
- Massive 字段/时段：[Custom Bars](https://www.massive.com/docs/rest/stocks/aggregates/custom-bars)
- Massive 套餐：[Pricing](https://massive.com/pricing)
