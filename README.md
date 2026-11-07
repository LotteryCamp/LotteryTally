# LotteryTally · PocketLedger 口袋账本

福州大学 2026 软件工程现场编程项目。项目计划实现一个仅在本机运行的个人收支记账网页应用，使用 Flask、Jinja2 和本地 JSON 文件。

> 当前状态：开发中。应用入口与首页骨架已准备；其他成员负责的存储、记账、流水和统计模块仍在集成。未接入的功能不会显示为已完成。

## 计划功能

- 记录收入和支出：日期、金额、类别、备注。
- 查看最近记录和完整流水。
- 按日期、类别、收支类型和金额范围筛选。
- 查看筛选结果对应的收入、支出、结余和笔数。
- 增强项：编辑/删除、CSV 导出、简单图表，以实际通过验收的版本为准。

## 环境

- Python 3.11 或团队统一的 Python 3 版本。
- Flask 3.x，项目元数据和依赖由 `pyproject.toml` 管理。
- 无需数据库、Node.js、CDN 或外部 API。

## 安装与启动

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe app.py
```

浏览器访问 `http://127.0.0.1:5000`。应用仅监听本机地址，关闭终端中的服务进程即可停止。

## 演示账本

演示数据为虚构内容，写入被 Git 忽略的 `data/demo_ledger.json`，不会覆盖仓库样例：

```powershell
.\.venv\Scripts\python.exe demo_data.py --output data/demo_ledger.json
.\.venv\Scripts\python.exe app.py --data-file data/demo_ledger.json
```

演示账本通过 `--data-file` 与正常用户数据隔离。目标演示文件已存在时，生成脚本会拒绝覆盖；确认要重建时加 `--force`。

正常运行时如未指定 `--data-file`，账本放在 `data/ledger.json`，该文件已加入 Git 忽略规则。真实个人账目不要提交到 GitHub。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s test -v
```

业务源码、模板与静态资源统一放在根目录的 `lotterytally/` 包中，测试放在根目录的 `test/`。使用 `pip install -e .` 安装项目后，测试导入的是项目包。测试覆盖范围随业务模块合并逐步扩展。

## 开发协作

- `main` 受仓库规则保护，功能改动从个人分支提交并通过 Pull Request 合入。
- 提交标题按团队约定使用 `<type>(<scope>): <subject>`，例如 `feat(home): add recent ledger summary`。
- 模块接口、人员分工和验收方式以团队确认的《PocketLedger 现场编程与技术实施方案》为准。
- Agent 可协助负责人实现本人模块；负责人需审阅、运行验证并能说明提交内容。团队成员不得代做或冒认他人贡献。
