# 陈常群：记账界面模块交付说明

对应《PocketLedger 现场编程与技术实施方案》第二节、第五节与第六节：

- **负责人**：陈常群（102401318）
- **负责模块与文件**：
  - `lotterytally/entry_views.py`：`GET、POST /entries/new`、`GET、POST /entries/<id>/edit`、`POST /entries/<id>/delete` 三条路由，负责收表单、调用账目服务落库、失败保留输入、成功跳转流水页
  - `lotterytally/templates/entry_form.html`：记账 / 编辑表单模板，含字段级错误提示、已停用类别的编辑保留、删除入口
  - `lotterytally/static/js/entry.js`：收支类型切换时联动类别下拉框、删除确认框（日期 / 类别 / 金额）
  - `test/test_entry_views.py`：模块自动化验收测试（14 项用例全部通过）

---

## 一、核心接口契约

### 1. 路由（`lotterytally.entry_views`）

| 方法 | 路由 | 行为 |
| :--- | :--- | :--- |
| `GET` | `/entries/new` | 渲染空表单；带 `copy_from=<id>` 时预填来源账目的金额 / 类别 / 备注，日期默认今天 |
| `POST` | `/entries/new` | 校验表单 → 创建账目 → 302 跳转 `/entries`（流水页）；失败返回 400 并保留输入、字段旁展示错误 |
| `GET` | `/entries/<id>/edit` | 渲染旧值；账目不存在返回 404 |
| `POST` | `/entries/<id>/edit` | 校验表单 → 更新账目（保留 `id` 与 `created_at`，刷新 `updated_at`）→ 302 跳转 `/entries` |
| `POST` | `/entries/<id>/delete` | 删除账目 → 302 跳转 `/entries` |

对外提供 `entry_bp`（Blueprint）、`register_entry_routes(app)`、`create_app(ledger_loader=None, ledger_saver=None, today=None, now=None)`，与统计模块的命名保持一致，应用入口 `app.py` 直接 `app.register_blueprint(entry_bp)` 或 `register_entry_routes(app)` 即可挂载。

### 2. 上游依赖（防御式导入约定）

| 依赖模块 | 合入 main 前 | 合入 main 后 |
| :--- | :--- | :--- |
| `lotterytally.entries`（佘博海，`create_entry` / `update_entry` / `delete_entry`） | 使用本模块内的 `_create_entry_local` / `_update_entry_local` / `_delete_entry_local` 后备实现 | 自动调用共享实现 |
| `lotterytally.validators`（何慧渊，`parse_amount_cents`） | 使用 `_parse_amount_cents_local`（与 filters 后备规则一致） | 自动调用共享实现 |
| `lotterytally.categories`（刘谦益，`list_categories`） | 使用 `DEFAULT_CATEGORIES` | 自动调用共享实现 |
| `lotterytally.storage`（李义东，`load_ledger` / `update_ledger`） | 直接读写 `data/ledger.json` | 走 `load_ledger` / `update_ledger` 写入锁 |

后备实现只服务于「依赖尚未合入」的并行开发阶段；对应模块合入并完成联调后应删除，避免双份维护（与 filters 模块的说明一致）。

---

## 二、与相邻模块的集成点

1. **流水页（张锦昊）**：新增 / 编辑 / 删除成功后统一 `redirect("/entries")`，因此流水页路由需保证 `/entries` 可访问。删除确认框由 `entry.js` 用事件委托实现——流水页的删除按钮只需复用同样的 `data-delete-entry` 表单与 `data-date` / `data-category` / `data-amount` 三个属性，并加载 `static/js/entry.js`，即可获得一致的确认体验，无需重复写确认逻辑。
2. **公共布局（魏之皓）**：`entry_form.html` 通过 `{% extends "base.html" %}` 复用布局，并依赖 `get_flashed_messages` 展示「已记下这笔账 / 修改已保存 / 已删除这笔账」提示。base.html 需保留导航与 flash 渲染位；模块内已内置等价兜底 base.html，保证独立可渲染。
3. **静态资源**：`entry.js` 位于 `lotterytally/static/js/entry.js`，由 `entry_bp` 的 blueprint 静态目录自行挂载到 `/static/entry/`，模板通过 `url_for('entry.static', filename='js/entry.js')` 引用。该方式不依赖应用层 `static_folder` 配置，应用入口 `app.py` 只需注册蓝图即可生效。

---

## 三、运行与验收

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install flask
.\.venv\Scripts\python.exe -m unittest discover -s test -v
```

2026-11-07 实际运行结果：`test_entry_views.py` 14 项用例全部通过；与仓库内 `test_csv_export.py` 一并运行共 49 项全部通过。覆盖方案验收项：

- **T02**：新增支出（金额 45.50 元 → 4500 分）与收入（3000 元 → 300000 分）均写入账本并跳转流水页。
- **T03**：金额 `0` / `-1` / `12.345` / `1e3`、不存在的日期（2026-02-30）、类别与收支类型不匹配、备注超 200 字，均返回 400、字段旁展示错误、账本未新增记录，且保留用户输入。
- **金额口径**：`45` / `45.5` / `45.50` 分别转换为 4500 / 4550 / 4550 分。
- **快速复制（P2）**：`/entries/new?copy_from=e1` 预填金额、类别、备注，日期默认今天而非来源日期。
- **编辑**：金额改为 20 元后 `amount_cents` 更新为 2000，`created_at` 保留原值，`updated_at` 刷新；编辑时已停用类别仍显示「（已停用）」。
- **删除**：确认后账目从账本移除。
- **类别联动**：新增表单只渲染当前收支类型下的启用类别（停用类别不出现在可选列表）。

本次交付由 Codex 辅助实现、复核并执行自动化测试，提交使用当前连接的 GitHub 账号身份（陈常群）。
