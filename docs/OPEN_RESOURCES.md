# 开源资源补齐与真实数据评估

核查日期：2026-09-20。本轮把 19 项开源工具、公开数据和官方规则整理到 `config/open_resources.json`，并把可用的数据入口做成了平台功能。代码许可证与行情使用权分开记录；没有购买订阅、注册账号或公开再分发行情。

## 已落地的入口

| 入口 | 能补齐什么 | 当前边界 |
| --- | --- | --- |
| BaoStock 0.9.3 | A 股未复权 OHLC、精确到股的量、人民币成交额、明确停牌行、上市日、现金分红支付日；中国指数和 ETF 备用来源 | 已匿名实测。ETF 文档仅从 2026-01-05 开始，不能覆盖成立以来；指数成分股量额不用于计算指数 VWAP。交易范围、配股和完整公司行动仍待验证 |
| 东方财富公开 K 线 | 港股、A 股 ETF、美股和 ETF 的行情与成交额；指数参考序列 | 实测苹果 2020 年拆股前后未复权价格。A 股“手”数有舍入；历史坏行隔离；断连时停止该源并保留检查点 |
| Yahoo Chart / yfinance 接口说明 | 美股、港股和指数长历史参考、原始拆股及除息事件 | Yahoo OHLC 已调整拆股，不能当真实未复权价格；证券参考序列禁止执行回测。除息日不冒充支付日；港股休市日零量占位行按日历排除并记录 |
| 中金所官方日 / 月文件 | 真实 IF/IH/IC/IM 月份合约、OHLC、结算价、成交量和金额 | 已下载并按乘数换算报价点数。官方统计含期转现、量额均为单边；尚未绑定完整挂牌、到期和历史保证金，不能宣称期货回测已完成 |
| 港交所官方报告与公告 | 成交额/股数交叉核对、特殊休市事实 | 已下载 2026-09-18 日报。按公告修正 2023-09-01 和 2023-09-08 全天休市；不从行情缺失推断休市 |

实际覆盖取决于当前本机数据目录和供应商返回内容；运行报告可能包含个人行情清单，不应加入公开源码仓库。对每个证券都应区分最早可用日、供应商上市日、候选均价、严格口径、停牌、缺口和执行阻止原因。失败和延后项目不应记作完成。BaoStock 指数发布前的回算历史应排除，避免提前用于决策。Yahoo 的指数映射和币种应逐项核实后才能用于研究。

## 关键证据与取舍

- [BaoStock 平台](https://www.baostock.com/mainContent?file=home.md)提供无需注册的接口；[日线字段](https://www.baostock.com/mainContent?file=stockKData.md)分别定义股数、人民币金额、未复权标志和停牌状态。[分红字段](https://www.baostock.com/mainContent?file=dividInfo.md)有实施与支付日期。本项目只自动转换资料齐全的税前现金分红；送转股、配股、零股、股息税仍保留缺口，`actions_verified` 不自动变真。
- [AKShare 股票文档](https://akshare.akfamily.xyz/data/stock/stock.html)和[源代码](https://github.com/akfamily/akshare)用于确认公开接口字段。核心运行不依赖整个 AKShare 包，只增加可选 BaoStock；独立评估环境安装过 AKShare 1.18.96。接口地址和协议字段已注明出处，没有拷贝 GPL 引擎代码。
- [yfinance 维护者说明](https://github.com/ranaroussi/yfinance/discussions/1682)指出普通 Close 也经过拆股调整。因此未使用 `auto_adjust=False` 来冒充未复权数据，没有重复执行其拆股事件。
- [中金所日统计](http://www.cffex.com.cn/rtj/)注明成交量为手、金额为万元，两者单边且含期转现。IF/IH 每点 300 元、IC/IM 每点 200 元；先把成交额转换为元，再除合约数及乘数。成交额公布精度造成的比值精度限制保留在来源记录中；该统计时段不会标成普通股票全日 VWAP。
- 港交所两次[台风全天休市](https://www.hkex.com.hk/News/Market-Communications/2023/2309012news?sc_lang=en)、[黑雨全天休市](https://www.hkex.com.hk/news/market-communications/2023/2309083news?sc_lang=en)与通用日历库不一致，已按官方证据更正新快照。旧快照仍可追溯。

## 美股最值得继续验证的免费方案

[Alpaca 官方 FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)明确区分 IEX 与 SIP：历史查询指定 `feed=sip` 且结束时间至少滞后 15 分钟，可不订阅付费计划；响应有 `vw`。其[套餐说明](https://docs.alpaca.markets/us/docs/about-market-data-api)列出历史从 2016 年起、免费账户的请求限额。需要账号和 API 密钥，当前没有账号实测，不能把“文档可行”写成“权限已验证”。它很可能比先购买全历史订阅更适合作为下一步验证，但不能补 1980—2015，也仍需核对盘前盘后和成交条件过滤。

没有找到并验证一个同时满足三地、全部标的、1980 至今未复权价格、完整公司行动、指定时段严格 VWAP 的单一免费来源。免费接口实际下载与完整需求之间的差距仍在覆盖表中，不会用插值、收盘价或指数拼接来隐藏。

## 已评估但不直接替换平台的资源

| 开源项目 / 官方资源 | 用途与采用决定 |
| --- | --- |
| [exchange_calendars](https://github.com/gerrymanoim/exchange_calendars) | 已采用；补充有证据的特殊休市，保持快照固定 |
| [FinanceDatabase](https://github.com/JerBouma/FinanceDatabase) | 证券映射候选，不能充当历史行情和退市库 |
| [Qlib](https://github.com/microsoft/qlib)、[Zipline Reloaded](https://github.com/stefan-jansen/zipline-reloaded) | 研究隔离与公司行动处理参考；不为引入框架而放弃现有已验证账本 |
| [Backtrader](https://github.com/mementum/backtrader)、[VeighNa](https://github.com/vnpy/vnpy)、[TqSdk](https://github.com/shinnytech/tqsdk-python) | 期货模型/合约接口参考；不附送完整历史行情。Backtrader 为 GPL-3.0，其他所列框架许可见登记表 |
| [Empyrical](https://github.com/quantopian/empyrical) | 绩效公式对照；不能取代外部现金流及融资账本 |
| [FRED SOFR](https://fred.stlouisfed.org/series/SOFR) | 融资参考利率候选；不等于券商融资利率，也不自动解决历史修订时序 |
| [ProShares TQQQ](https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq) | 真实产品成立日和每日三倍目标证据；不制造成立前杠杆 ETF 历史 |
| [HKEX 恒指期货规格](https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index/Hang-Seng-Index-(HSI)/Hang-Seng-Index-Futures?sc_lang=en)、[CME ES 规格](https://www.cmegroup.com/markets/equities/sp/e-mini-sandp500.contractSpecs.html) | 合约字段证据；当前规格不能倒填历年保证金，公开规格不等于行情免费 |

## 本机使用

网页“数据与覆盖”有公开资源面板，可下载六个代表样本或当前完整股票池，后台子进程执行，状态逐标落盘，支持同请求续传。失败不删除已入库版本。候选均价计数与严格 VWAP 计数分开展示，数据集可以按代码/名称/市场筛选。

命令行（在项目目录运行；首次安装使用 `scripts/install.ps1`）：

```powershell
.\.venv\Scripts\python.exe -m investment_lab.cli fetch-open-data --scope sample
.\.venv\Scripts\python.exe -m investment_lab.cli fetch-open-data --scope universe
.\.venv\Scripts\python.exe -m investment_lab.cli fetch-open-data --scope universe --reference-market US
.\.venv\Scripts\python.exe -m investment_lab.cli fetch-open-data --scope universe --reference-market HK
.\.venv\Scripts\python.exe -m investment_lab.cli fetch-open-data --scope universe --instrument CN:600030
.\.venv\Scripts\python.exe -m investment_lab.cli fetch-open-data --scope universe --cn-provider baostock --instrument CN:INDEX_510300 --instrument CN:510050
.\.venv\Scripts\python.exe -m investment_lab.cli fetch-cffex-day 2024-09-02
.\.venv\Scripts\python.exe -m investment_lab.cli fetch-cffex-month 2024-09
.\.venv\Scripts\python.exe -m investment_lab.cli fetch-cash-actions SNAPSHOT --start 2024-01-02 --end 2025-12-31
```

`--data` 位于子命令前，可指定隔离目录。`--start/--end` 控制请求边界；同一区间重跑复用校验通过的快照，`--refresh` 主动重查。请求日期不会被写成真实覆盖起点。普通每日增量可在 `providers.json` 使用 `baostock`、`eastmoney`、`yahoo`；原有 EODHD/Tushare 入口仍可使用。公司行动导入另成新快照，不能把有限年份导入标成全历史完整。

供应商子进程有总超时和进程树终止；SDK 全局 socket 不进入网站进程。分页失败不允许假装成功 EOF。连续三次供应商失败后，本轮其余目标延后，换其他独立来源继续；不通过更换身份/代理规避限制。后续可重新续传。

原始文件、SDK 返回数据、异常行、不可变快照及核查证据在本机运行数据中保存；代码仓库只含适配器、资源登记、覆盖统计和合成测试夹具，不包含行情全集。

参考序列使用独立名称，不能覆盖候选未复权数据。已归档 17 份公开来源证据（官方字段说明、期货日/月文件、港交所报告、代码许可证和字段实现），其内容哈希对象随普通一致性备份保存。隔离评估数据可用 `scripts/import_open_snapshots.py --source SOURCE --data DESTINATION` 导入：先备份目标，再验证内容哈希并保留全部历史版本与原始父对象，不直接复制运行中的 SQLite。

本机当前已配置114个已下载标的的无账号增量目标；网页“检查并补数”可手动执行，不会自动创建计划任务。三地代表标的增量检查已通过。日期卡片展示快照日历范围，实际行情起止与缺口以“检查覆盖与口径”及逐标表为准。
