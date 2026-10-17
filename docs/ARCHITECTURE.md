# 当前架构

项目包含 Windows 本机版和 GitHub Pages 静态浏览器版，共享 Python 引擎及 22 种内置策略目录。本机版由 FastAPI 提供仅监听回环地址的 HTTP 接口，原生 HTML/CSS/JavaScript 提供网页，本机静态资源不依赖外部 CDN。数据、回测和研究模块不依赖网页，可从 CLI 运行。计算以独立 Python 工作进程执行，网页服务监控时间和进程树内存，提供取消。

Pages 版由白名单构建发布静态文件，从 jsDelivr 下载固定版本 Pyodide 及运行包，在 Web Worker 内计算，以 IndexedDB/localStorage 保存访客自己的快照、研究和配置。它没有本机后端、云账户或同步，也不执行访客上传的任意 Python。首次运行需联网加载资源，完整离线和跨设备等价验收仍待完成，见 [HOSTING.md](HOSTING.md)。

网页前端是原生脚本，没有构建步骤。两版共用 `src/investment_lab/web/static/` 下的模块：`theme.css`（配色、字体和控件，含深浅主题与涨跌色偏好；`theme-boot.js` 在首屏前应用偏好）、`ui-utils.js`（格式化、CSV、偏好与运行标记）、`strategy-forms.js` 与 `strategy-editor.js`（参数表单和按策略保存的草稿原文）、`snapshot-selection.js`（快照选择规则，含缺失阻断）、`result-views.js`（指标、明细表、曲线定义与比较）、`charts.js`（SVG 曲线）。各模块在 Node 中可直接加载测试，在浏览器中注册为全局对象。本机版以 `app.js` 提供接口调用、提示、地址路由和任务进度，页面逻辑分在 `view-research.js`、`view-runs.js`、`view-manage.js`；本机内容安全策略只允许同源脚本和样式，页面不使用内联脚本或样式属性。Pages 构建按白名单复制共享模块，`pages/app.js` 只保留浏览器存储、Worker 与本版特有的流程。

本机网页通过 `/api/runs/{id}/progress` 轮询任务状态，不读取结果，因此不计入留出查看次数；`/api/runs/{id}/table` 与 `/api/runs/{id}/export` 同样不计数，只有打开完成的留出详情才计数。代码指纹（`engine_hash`）覆盖 `src` 下的 Python、JSON 等源文件，但不含只由浏览器加载的 `web/static`，纯页面更新不影响已有运行的复现。

SQLite 是任务、数据集当前指针、留出使用记录和审计的唯一可变索引。schema=1，打开不兼容 schema 时拒绝写入。每次写操作使用应用锁和短事务；SQLite 连接显式关闭。

行情保存在 `market/<sha256>.json.gz`，按证券/年度生成不可变分区。`raw` 保存原始响应及代码/股票池旧版本。`manifests` 保存数据集清单；数据库中的清单使用同一内容哈希。新快照共享未变分区，旧清单及分区不清理，因此仍可重现旧输入。未采用另一套可变行情数据库。

本机回测任务：读取确定快照 → 冻结自定义策略项目内所有 `.py`（内置策略冻结 `examples.py` 和 `catalog.json`）、参数、股票池版本、引擎源、依赖锁与实际已装依赖 → 子进程运行 → 单独保存结果。CLI `run` 与网页均使用 JobManager。直接 Python 调用 simulate/execute_run 是测试/受信开发接口，不是安全沙箱。

多快照请求先通过 `data/compose.py` 校验兼容性，产生只引用原分区的组合清单，再沿用同一个运行管线。组合写入datasets/manifest及审计，不写heads，来源选择列表不会被临时组合占满；父快照、公司行动、完整日历和证券配置均保留。运行区间受各来源共同覆盖边界约束，日历并集保留缺口证据。无需数据库迁移。

策略 API 仅交付当前可用历史副本和账户快照。撮合引擎独立读取执行日数据。恶意 Python 通过文件/网络主动获取未来信息不在当前可信代码模型的保护范围。

备份使用内容寻址对象库与完整恢复清单。SQLite 使用 Backup API；文件快照在同一应用写锁下建立。恢复校验哈希，在不存在的新目录建立数据库并检查完整性。恢复不会切换现有服务。自动轮换目前仅预览。

当前规模选择压缩 JSON 是可读性、依赖数量与实现可靠性的权衡。若真实全股票池加载或查询性能不足，再迁移 immutable partitions 的编码到 Parquet；接口不改变，迁移前另做快照和对照验证。
