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

对外提供 `entry_bp`（Blueprint），应用入口 `app.py` 通过 `app.register_blueprint(entry_bp)` 挂载（已在 `app.py` 的 `BLUEPRINTS` 中登记）。

### 2. 依赖（与实施方案 5.2 节一致）

| 依赖模块 | 用途 | 导入方式 |
| :--- | :--- | :--- |
| `lotterytally.entries`（佘博海） | `create_entry` / `update_entry` / `delete_entry`，抛 `EntryValidationError(errors)` / `EntryNotFound(entry_id)` | 模块顶部直接导入，路由直接调用并捕获异常 |
| `lotterytally.categories`（刘谦益） | `list_categories(ledger, entry_type, active_only)` | 模块顶部直接导入 |
| `lotterytally.storage`（李义东） | `load_ledger()` 读账本快照 | 延迟导入 `_storage()`；缺失时应用仍能启动，只有访问记账页才报错 |

本模块不再内置任何「本地后备实现」。金额转换、字段校验、类别匹配统一交给 `entries` 服务，路由只负责：收表单 → 调服务 → 捕获 `EntryValidationError`（回填表单 + 字段错误）→ 捕获 `EntryNotFound`（404）→ 成功跳转流水页。

---

## 二、与相邻模块的集成点

1. **流水页（张锦昊）**：新增 / 编辑 / 删除成功后统一 `redirect("/entries")`，因此流水页路由需保证 `/entries` 可访问。删除确认框由 `entry.js` 用事件委托实现——流水页的删除按钮只需复用同样的 `data-delete-entry` 表单与 `data-date` / `data-category` / `data-amount` 三个属性，并加载 `static/js/entry.js`，即可获得一致的确认体验。
2. **公共布局（魏之皓）**：`entry_form.html` 通过 `{% extends "base.html" %}` 复用布局。`base.html` 顶部导航依赖 `navigation` 变量，本模块在每次渲染时都会传入（只保留当前应用实际注册了的页面，与应用入口 `NAV_ITEMS` 保持一致）。
3. **静态资源**：`entry.js` 位于 `lotterytally/static/js/entry.js`，由应用层统一静态目录挂载到 `/static/`，模板通过 `url_for('static', filename='js/entry.js')` 引用（与 `charts.js` 等队友静态资源同一套约定）。

---

## 三、运行与验收

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install flask
.\.venv\Scripts\python.exe -m unittest discover -s test -v
```

2026-11-07 实际运行结果：`test_entry_views.py` 14 项用例全部通过。覆盖方案验收项：

- **T02**：新增支出（金额 45.50 元 → 4550 分）与收入（3000 元 → 300000 分）均写入账本并跳转流水页。
- **T03**：金额 `0` / `-1` / `12.345` / `1e3`、不存在的日期（2026-02-30）、类别与收支类型不匹配、备注超 200 字，均返回 400、字段旁展示错误、账本未新增记录，且保留用户输入。
- **金额口径**：`45` / `45.5` / `45.50` 分别转换为 4500 / 4550 / 4550 分。
- **快速复制（P2）**：`/entries/new?copy_from=<id>` 预填金额、类别、备注，日期默认今天而非来源日期。
- **编辑**：金额改为 20 元后 `amount_cents` 更新为 2000，`created_at` 保留原值，`updated_at` 刷新；编辑时已停用类别仍显示「（已停用）」。
- **删除**：确认后账目从账本移除。
- **类别联动**：新增表单只渲染当前收支类型下的启用类别（停用类别与另一类型的类别不出现在可选列表）。

本次交付由 Codex 辅助实现、复核并执行自动化测试，提交使用当前连接的 GitHub 账号身份（陈常群）。
