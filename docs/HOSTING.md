# 公网网站与 GitHub Pages

## 当前状态

投资研究室现有版本仍是单用户 Windows 本机应用，不可直接公开。服务只绑定 `127.0.0.1`，当前进程内只有一个数据目录和一套 SQLite 索引；网页 API 没有登录或租户授权。网页还允许上传 Python 策略、触发数据下载和回测，并管理本机备份。自写 Python 策略是可信本机代码模型，不是安全沙箱。把现有 FastAPI 服务直接转发到公网，可能让访客读取或改动同一用户数据、消耗计算/网络资源，或在服务器进程权限下运行代码。

## GitHub 免费服务能做什么

[GitHub Pages 是静态网站托管](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)：它可发布 HTML、CSS、JavaScript 和静态生成文件，不能启动当前项目所需的 FastAPI、Python 回测工作进程或 SQLite 数据库。因此，Pages 可以承载公开项目介绍、文档、预先生成的示例或完全在浏览器内运行的演示，但单独不能承载当前完整应用。

[GitHub Free 的 Pages 要求公开仓库](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)。网站本身也会公开可访问；GitHub 说明访问 Pages 时会记录访客 IP 用于安全目的。发布源码前必须确保仓库的全部 Git 历史里不含运行数据、个人策略、回测归档、密钥和日志。GitHub Actions 适合构建/部署这种静态页面和运行自动化任务；它的运行器是任务型虚拟机，不是常驻网站后端。[公开仓库标准运行器免费；私有仓库使用免费分钟额度并受计划限制](https://docs.github.com/en/actions/concepts/billing-and-usage)。

[GitHub Pages 使用限制](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)明确表示 Pages 不可作为在线业务或商业 SaaS 的免费主机。当前部署定位为公开开源项目的静态浏览器工具：没有云账户、服务端回测、云端用户数据或交易执行。若后续把它改成商业 SaaS、金融交易服务或依赖服务器提供的在线业务，应先迁移到合适的托管方案，不继续把 Pages 当作免费 SaaS 主机。

## 安全上线门槛

要让访客实际运行回测，需要先设计并验证一套独立的多用户服务：身份验证和账户/租户隔离、每位用户隔离的数据及回测归档、共享行情与私人配置的边界、请求速率和 CPU/内存/并发限额、可靠持久化与备份，以及不允许任意 Python 代码在服务账户权限下运行的隔离策略执行环境。部署还需要确定个人行情和用户输入的存储位置、保留/删除方式及运营成本。

GitHub Pages 可作为该服务的静态前端；真正的 API/工作队列/持久化存储必须部署在可运行后端的环境中。当前公开浏览器版让每位访客在自己的浏览器中运行固定内置策略，但不等于完整本机平台已经迁移为多人云端服务。扩大公开文件或改变部署结构时，仍需审查公开历史和数据边界。

## 浏览器端公开版当前状态

已增加可重复运行的浏览器冒烟页 `tests/browser/pyodide-engine-smoke.html`。它从项目源码加载当前 Python 引擎的时序、账本、撮合、指标和数据组合模块，在 Web Worker 内以固定版 Pyodide `314.0.7` 和 NumPy/tzdata 执行五个交易日的合成 VWAP 案例。2026-09-24 实测通过：一笔成交、期末权益 `1000`、现金 `500.00`，结果摘要与本机 CPython 完全相同：`efa17a097b1d7087ec3cf34d0f4270dd52068591e3100f0d85fc2fc1a551dfc3`。工作线程把计算移出页面主线程；Pyodide 官方文档也建议长计算放入 Web Worker。

浏览器版已发布到[投资研究室浏览器版](https://xumouren114514.github.io/investment-lab/)。源码在 `pages/`，由 `scripts/build_pages_site.py` 按允许清单生成到忽略跟踪的 `build/pages/`。构建会拒绝白名单外的遗留文件和符号链接，避免把构建目录中的意外文件加入公开制品。GitHub Pages 来源已设为 GitHub Actions；每次 `main` 更新会自动构建并部署。工作流采用最小化 job 权限、固定到完整提交 SHA 的官方 Actions，使用 Ubuntu 24.04 运行器，并由 Dependabot 每周检查动作更新。它只打包显式列出的网页与 Python 核心文件，不复制本机应用数据目录。

2026-09-24 在本地静态服务器和真实浏览器中完成了端到端冒烟：导入五日合成演示、写入浏览器 IndexedDB、由 Pyodide Worker 运行买入持有、显示进度/预计剩余时间、指标/权益图/成交，并保存本机历史；控制台无错误。项目测试还核对了静态构建允许清单和多快照纯函数组合。浏览器版已经支持快照 `.json.gz` 导入、本机研究备份/恢复、复选多快照、八种内置策略、价格研究模式和参数草稿。此验证样例是合成数据，只证明流程，不代表真实行情质量或投资效果。

2026-09-24 线上冒烟使用五日合成样本完成了浏览器端买入持有研究，进度到 100%，产生 1 笔成交；这只验证静态部署和合成流程，不代表真实行情结果。浏览器版不是全部本机平台功能的迁移版。需要真实数据时，访客需从本机投资研究室导出快照包，再导入自己的浏览器；来源许可由分享者自行核验。没有云账户、跨设备同步或服务器持久化。网页只运行固定内置策略，禁用任意 Python 策略上传/执行。滚动起点与留出流程、完整交易规则一致性、真实快照导入、跨浏览器兼容和不同设备上的性能仍需专项验证，不能据此宣称与本机平台完全等价。

固定内置策略在 Worker 中运行并不自动构成安全沙箱。本版不接受任意 Python；增加自定义策略前必须先完成代码隔离和浏览器权限审查。研究数据、配置和结果保存在访问者浏览器的 IndexedDB/localStorage，丢失浏览器站点数据会一并删除；建议定期导出备份。Pyodide 固定版本由 jsDelivr 加载，CDN 可见常规网络请求信息，但请求不包含页面内快照或回测结果。首屏下载量、完整离线能力、CDN 故障恢复、主流浏览器兼容和缓存策略尚未专项测试；上线后仍需按目标访客设备补齐这些检查。

本地复测：`.venv\Scripts\python.exe scripts\build_pages_site.py --output build\pages`，然后以静态服务器提供 `build/pages`。生产工作流会做相同构建。引擎的最小垂直切片还可按旧冒烟页说明复测；需要联网下载固定版本 Pyodide 和 wheel。该浏览器版与 Windows 桌面入口并列：前者便于他人直接打开并在各自浏览器研究，后者仍启动用户自己的本机 FastAPI/SQLite。

## 已核对的免费后端限制

[Render 免费 Web Service](https://render.com/docs/free) 支持 Python/FastAPI，但无请求 15 分钟后休眠、唤醒约需一分钟；免费实例文件系统为临时盘，SQLite 会在重启、重部署或休眠时丢失。免费 Postgres 计划也会在 30 天后到期且无备份。因此它适合短期预览，不适合保存用户研究数据或作为可复现研究的唯一存储。

[Hugging Face Spaces 免费 CPU](https://huggingface.co/docs/hub/spaces-overview) 可运行 Python/Docker 容器，默认 2 vCPU、16 GB RAM，但 50 GB 磁盘不持久，闲置会休眠；其配置文档注明持久存储功能已不可用。公开 Space 会公开源代码；免费账户没有“源码私有、应用公开”的 Protected 可见性。它同样不能直接保存个人策略、行情快照与运行归档。

所以目前核实到的 GitHub Pages、Render Free 和 Spaces 免费能力都无法同时满足“完整回测、可靠云端持久化、公开多人访问、免费”四项。若优先满足免费与数据隔离，浏览器本地架构值得继续验证，但需完成迁移并解决 Pages 使用限制、任意 Python 策略隔离和数据来源许可；若需要跨设备云端保存/共享账户，则必须有带持久存储的多租户后端，免费额度不能视为长期保证。

## 更新发布约定

每次项目更新完成后，先按修改范围做逻辑与语法检查；代码只运行最相关的少量测试，文档/配置无需跑 pytest；随后运行 `scripts/publish_update.ps1`。该脚本会检查明确列出的文件、更新桌面快捷方式、创建本地提交并推送当前分支到 GitHub，不会强制推送或上传未列出的路径。远程尚未配置、认证失败或推送冲突时，保留本地提交并明确标记为未上传；按远程分支状态解决后再普通推送。

示例：

```powershell
.\scripts\publish_update.ps1 `
  -Message "fix: correct progress estimate" `
  -Paths "src/investment_lab/jobs.py", "tests/test_jobs_and_update.py", "CHANGELOG.md" `
  -Tests "tests/test_jobs_and_update.py::test_progress_estimate"
```

文档或配置更新可以不传 `-Tests`；Python/JavaScript 代码改动必须传相关 pytest 文件或测试节点。`TASK_STATE.md`、`docs/changes/`、个人需求文档、个人 `config/universe.yaml`、个人策略/研究和验证证据保存在本机，不上传；只把通用用户文档与 `CHANGELOG.md` 放入公开版本。初始公开提交使用 `.public-history-baseline` 标记，发布脚本会检查标记和历史路径。旧的本机历史只保留在本机；GitHub 远端和 Pages 已配置完成，后续发布使用普通推送并由工作流自动更新网站。
