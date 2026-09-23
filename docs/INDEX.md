# 文档索引

- 项目范围与协作方式：`../README.md`、`../AGENTS.md`。
- 引擎、账本、价格与时序约束：`ENGINE.md`。
- 数据源和授权注意事项：`DATA_SOURCES.md`、`OPEN_RESOURCES.md`。
- Python 策略 API 与示例：`STRATEGIES.md`。
- 系统结构和设计决定：`ARCHITECTURE.md`、`decisions.md`。
- Windows 安装、更新、备份与恢复：`OPERATIONS.md`。
- GitHub Pages、浏览器本地存储和公网安全边界：`HOSTING.md`。
- GitHub Pages 应用源码：`../pages/`；白名单构建：`../scripts/build_pages_site.py`；部署流程：`../.github/workflows/pages.yml`。
- 行情快照导入/导出及本机 API：`../src/investment_lab/web/app.py`、`../src/investment_lab/web/static/portable-data.js`。
- 相关测试：`../tests/`。默认运行与改动直接相关的少量测试，不把合成验证当作真实行情验收。

本机运行状态、运行记录、验收结果和个人策略不属于公开文档；按需保存在本机数据目录或本机状态文件中。
