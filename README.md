# 投资研究室

个人投资研究与日线回测工具，包含 Windows 本机版和独立浏览器版。浏览器版由静态文件构成，可部署到 GitHub Pages；不需要服务器账户或云端数据库。

## 浏览器版

**在线试用：**[投资研究室浏览器版](https://xumouren114514.github.io/investment-lab/)。

浏览器版从数据快照包导入行情，在 Web Worker 中运行固定版本的 Python 引擎与内置策略，并将数据、配置和结果保存在当前浏览器。可选择多份快照、配置费用/现金流/研究区间、查看进度与结果，并导出本机备份。页面不会上传用户的快照、策略或回测结果；清除该网站的浏览器数据会删除本机保存内容。

它不会运行用户上传的任意 Python，也没有云账户、跨设备同步或共享存储。请核实行情数据的授权范围；研究输出取决于数据质量及交易假设，不代表实际成交或投资建议。

本地构建和预览：

```powershell
python scripts/build_pages_site.py --output build/pages
python -m http.server 8766 --directory build/pages
```

GitHub Pages 已配置为 GitHub Actions 来源；推送 `main` 后会自动构建并部署浏览器版。发布工作流和逐步更新方式见 `.github/workflows/pages.yml`、`docs/HOSTING.md` 与 `scripts/publish_update.ps1`。

## Windows 本机版

本机版提供 FastAPI 页面、SQLite 索引、后台回测、数据更新和 Python 策略。它为单用户设计，只绑定本机回环地址，**不要直接转发或公开暴露**。安装依赖后可以运行：

```powershell
python -m investment_lab.cli init --demo
python -m investment_lab.cli serve
```

默认网页地址为 `http://127.0.0.1:8765`。开发和运行环境要求见 `scripts/install.ps1`、`requirements.lock` 及 `docs/OPERATIONS.md`。自定义 Python 策略会以本机进程权限执行，仅运行自己信任的代码。

## 架构和贡献

- 当前接口、时序和数据质量约束：`docs/ENGINE.md`、`docs/STRATEGIES.md`、`docs/DATA_SOURCES.md`。
- 浏览器托管范围及限制：`docs/HOSTING.md`。
- 受影响的 Python 测试可使用项目 `.venv` 中的 pytest；浏览器数据工具测试：`node --test tests/test_portable_data.cjs`。
- 更新和回退原则：`AGENTS.md` 与 `scripts/publish_update.ps1`。

代码仓库不得包含本机行情、个人策略、运行结果、验证证据、日志、凭据或备份。不要将本机 API 暴露到公网。
