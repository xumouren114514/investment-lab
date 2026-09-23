PowerShell 示例中的 $ProjectRoot、$DataDirectory、$BackupDirectory 和 $RestoreDirectory 代表当前计算机上的项目/数据路径；按实际位置设置这些变量后执行命令。$env:USERPROFILE 是当前登录用户目录。

# 安装、更新、备份与恢复

## 启动与运行

日常双击桌面的 **投资研究室**。入口会自动检查并启动本机服务，然后使用现有 Edge（无 Edge 时使用 Chrome）打开独立应用窗口。重复打开复用同目录、同数据目录的服务；无需先打开终端或 Codex。关闭应用窗口后，后台服务仍会保留，重启电脑后再次双击即可。

公开仓库只提供通用配置模板，不包含个人标的池。新克隆的项目会在个人配置缺失时读取 `config/universe.example.yaml`；运行 `python scripts/bootstrap_config.py` 可复制模板到本机配置。脚本会保留已有的 `config/universe.yaml`，不会覆盖自定义标的池。

创建或修复桌面入口：

```powershell
cd $ProjectRoot
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\install_desktop.ps1
```

此入口依赖当前项目及 `.venv`，不是可随意搬走的单文件程序。项目移动后要重新创建入口；安装器不会覆盖指向其他目标的同名快捷方式。没有新增开机自启动。桌面窗口使用独立浏览器配置 `%LOCALAPPDATA%\InvestmentLab\browser-profile`。

启动失败会显示具体原因，可点“重试”或“打开日志文件夹”。默认日志位于 `$DataDirectory\logs`：`desktop-launcher.log` 记录入口、`desktop-server.log` 记录后台服务。`state/desktop-last-launch.json` 记录最后一次成功打开，`state/desktop-service.json` 记录最近一次由入口新启动的服务；其中 PID 仅供诊断，不能据旧 PID 盲目结束进程。端口被其他应用占用时入口会停止，不会关闭其他服务。

仅检查或启动后台服务（诊断用，不打开窗口）：

```powershell
.\.venv\Scripts\python.exe scripts\desktop_app.py --ensure-server
```

开发时仍可使用原前台启动方式：

```powershell
cd $ProjectRoot
scripts\install.ps1
scripts\start.ps1
# 浏览器打开 http://127.0.0.1:8765
```

端口占用时先确认已有服务，不盲目结束其他进程。可运行 `scripts\start.ps1 -Port 8766`。以 Ctrl+C 停止前台服务；已关闭浏览器不会停止服务。

```powershell
.\.venv\Scripts\python -m investment_lab.cli list
.\.venv\Scripts\python -m investment_lab.cli run config\example_run.json
.\.venv\Scripts\python -m investment_lab.cli reproduce <实际run_id>
```

example_run 的 snapshot 必须替换为实际 ID，避免误用演示数据。每次运行目录保存 request.json、strategy/、result.json、worker.log；失败有 error.json。网页可取消运行；默认 600 秒/2048 MB 进程树内存上限。自写可信代码不是安全沙箱。

## 标准化数据导入

```powershell
.\.venv\Scripts\python -m investment_lab.cli import-json D:\你的目录\dataset.json
```

JSON 顶层与 data/demo.py 的 demo_payload 结构一致：name、securities（按证券 ID 映射）、bars、sessions、actions、synthetic、source、可选 raw。真实数据 synthetic=false，记录来源、交易所/币种/上市日及验证证据。日期 ISO，available_at 必须含时区。

bars 必含 symbol/date/open/high/low/close/volume/source/available_at；VWAP 另含 vwap_value、vwap_method、vwap_session、quality_status。经过核实才标记 verified，amount_volume_verified 必须同成交场所/时段/货币/股数单位。若未验证，写 unverified/unavailable；不要为了绕过检查修改标志。停牌记录显式 status=suspended，估值仍须有已说明来源的价格；不得从缺失日自动推断停牌。

actions 支持 split 的 ratio 和 dividend 的每股 amount/pay_date。配股及未知碎股对价目前拒绝处理。期货需另提供真实合约参数与 settlement；不得将连续合约当真实月份成交。

本机网页“数据覆盖”列表可按单个快照下载压缩的数据包（`investment-lab-snapshot` v1，`.json.gz`），也可选择数据包导入；静态浏览器版同样可以导入此格式并保存在当前浏览器。包内含不可变清单与标准化日线，不含原始供应商响应；导出与导入都会检查常见凭据字段和令牌格式，命中时拒绝处理且不回显字段值。导入会验证清单摘要和数据格式，在本机生成新的不可变快照；原始 ID 可能因去掉原始响应对象而变化。组合快照需分别导出其原始来源快照。JSON 最大 128 MiB，浏览器端 gzip 文件最大 250 MiB；更大的数据请按标的拆分。导出文件进入用户浏览器的下载目录，不会自动上传；分享前仍须核对供应商数据许可。所有导入和备份都由浏览器本地完成，不会自动上传到服务器。

## 供应商与补数

程序不会自动创建供应商账户或购买数据。公开样本命令只使用官方公开 demo key（非用户凭据）：

```powershell
.\.venv\Scripts\python -m investment_lab.cli fetch-public-sample
```

正式凭据仅在本机环境提供 `EODHD_API_TOKEN` 或 `TUSHARE_TOKEN`。不要放进 Git、普通 JSON 或聊天。日常计划任务需要在其运行账户环境中安全提供凭据；本版没有密钥加密存储 UI，普通备份不包含密钥。

将 config/providers.example.json 复制到数据目录 local_config/providers.json 后按实际账户与已核实证券填写。适配器目前有 EODHD/eod 和 Tushare/daily、hk_daily、fund_daily、fut_daily。后者只有 daily 的手/千元换算已实现；其余口径默认未验证，不产生可用 VWAP。公司行动/停牌补齐需使用标准化导入，不宣称适配器已自动覆盖。

```powershell
.\.venv\Scripts\python -m investment_lab.cli update
scripts\register_daily_task.ps1              # 只展示计划
scripts\register_daily_task.ps1 -Install     # 注册当前账户任务
```

每日香港时间 09:30/22:30 唤起补数入口；是否补哪个交易日由 US/HK/CN 的时区、交易日历、供应商保守发布时间与检查点决定。StartWhenAvailable 负责错过后尽快运行，电脑关机时不能更新。注册不是长期运行验收，必须观察实际历史后再宣称可靠。

初次数据按单证券分批保存、失败证券不影响已成功证券；Tushare 分页重试并限速。中断后复用已成功数据集并重取最近 14 个日历日。不宣称单个网络请求内部断点续传。full_revision_audit=true 做更早历史复核；默认保留旧快照。请求成功但空数据或有缺口会单独报告，缺口不补造。last-update.json 保存新增/修订/失败，checkpoint-*.json 保存有效检查点。

## 一致性备份

```powershell
.\.venv\Scripts\python -m investment_lab.cli backup
.\.venv\Scripts\python -m investment_lab.cli retention-preview
.\.venv\Scripts\python -m investment_lab.cli daily-backup
```

默认 $BackupDirectory，与输入目录分离。SQLite Backup API、应用写锁、SHA-256 完整恢复清单、内容寻址去重。行情、旧快照、用户策略、运行结果和非敏感配置入备份；缓存、活动日志、虚拟环境、密钥不入普通备份。api_key/token 等敏感键会使对应配置被跳过并写入结果。

daily-backup 比较用户策略、非敏感配置、行情清单和已完成结果的内容指纹，变化后才创建恢复点；日常入口 daily.ps1 在补数之后执行此检查。已登记审计/运行日志的变化不会单独导致重复完整备份。供应商部分失败返回非零退出码，原有快照仍保留。

保留策略已提供预览，没有自动删除入口。7日/4周/3月、最近3点和固定保护是设计默认；稳定发布引用和增量引用关系在启用删除前另审。当前全部已建恢复点都保留。

local_config/backup.json 可设 {"root":"E:\\你选择的备份目录"}，目标须为空的独立受管目录。切换目标只影响之后的备份，不自动删除旧副本；可向另一物理磁盘再运行一次完整恢复点保存。不能把 D 盘另一文件夹说成抗磁盘故障副本。

## 独立恢复与回退

```powershell
.\.venv\Scripts\python -m investment_lab.cli restore <backup_id> $RestoreDirectory
.\.venv\Scripts\python -m investment_lab.cli --data $RestoreDirectory reproduce <原run_id>
scripts\start.ps1 -Port 8766 -DataDirectory $RestoreDirectory
```

恢复前校验所有对象，目标已存在则拒绝。恢复后运行数据库完整性/外键检查，加载数据快照；还必须复现回测并启动查询，才算完整演练。恢复命令本身不会假称回测已复现。验证成功后再停止当前写入、切换 DataDirectory；新数据与个人策略仍保留在原目录。

常规代码撤销用 git revert；从旧标签验证用 `git worktree add <新目录> <标签>`，不使用 reset --hard。每次启动检查 schema，不兼容旧版本拒绝写入。可在对应版本目录按保存的 requirements.lock 重建独立 .venv 后运行旧数据快照；标签不移动。

严格复现会核对依赖锁原始字节。Git 检出可能改变换行格式，重建时将恢复后的 `runs/<原run_id>/requirements.lock` 复制到独立版本目录的 requirements.lock，再用该锁安装依赖；该动作只在独立版本目录进行，不覆盖正在运行的版本。实际安装依赖版本也会再次核对。

发布时执行 `release-bundle <版本>` 保存 Git 历史。离线依赖包若有归档，应同时记录文件校验值和来源；没有归档时，单有版本号不保证未来还能下载。

## 每次更新桌面入口与 GitHub

每次项目文件修改后按影响范围检查逻辑和语法；代码只运行与本次改动最相关的少量测试，不默认运行全量测试，文档/配置更新无需运行 pytest。通过后在项目根目录用明确的提交说明、变更文件和测试节点执行：

```powershell
.\scripts\publish_update.ps1 `
  -Message "fix: describe the change" `
  -Paths "src/investment_lab/jobs.py", "tests/test_jobs_and_update.py", "CHANGELOG.md" `
  -Tests "tests/test_jobs_and_update.py::test_relevant_behavior"
```

该脚本对 Python 变更做语法编译，对 JavaScript 变更做 `node --check`，运行显式传入的少量 pytest 节点，刷新 `$env:USERPROFILE\Desktop\投资研究室.lnk`，只暂存列出的文件，然后本地提交并普通推送当前分支。文档等非代码更新可省略 `-Tests`。本地提交和推送分开；如果 GitHub 远程、认证或网络失败，本地提交会保留且脚本会明确报错，不会 force push。

首次公开前必须审查 Git 历史并创建隐私清理后的初始提交，再配置不含凭据的 `origin`。不要推送本机状态、验证证据、行情、个人策略、运行结果、日志、备份或密钥。公网页面的范围与安全门槛见 HOSTING.md；当前单用户 FastAPI 服务不应被直接部署到公网。
