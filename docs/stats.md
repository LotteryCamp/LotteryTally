# 王智钦：收支统计与 CSV 导出接入模块交付说明

对应《PocketLedger 现场编程与技术实施方案》第二节、第四节与第五节：
- **负责人**：王智钦（102401609）
- **负责模块与文件**：
  - `lotterytally/summary.py`：收支汇总、分类小计、每日趋势（缺失日补零）、月度同期对比计算
  - `lotterytally/stats.py`：`GET /stats` 统计页路由与 `GET /stats/export.csv` 同源筛选 CSV 导出路由
  - `lotterytally/templates/stats.html`：统计看板、组合筛选栏、分类占比表、月度对比与图表容器模板
  - `test/test_summary.py`、`test/test_stats.py`：模块自动化验收测试（18 项用例全部通过）

---

## 一、核心接口契约

### 1. `lotterytally.summary`

| 函数签名 | 职责与返回值 |
| :--- | :--- |
| `summarize(entries, categories=None) -> dict` | 计算筛选后完整记录集合的 `count`、`income_cents`、`expense_cents`、`balance_cents`（`income_cents - expense_cents`），以及按支出降序排列的 `by_category` 列表（含 `category_id`、`category_name`、`amount_cents`、`amount_yuan`、`count`、`percent`、`share_text`） |
| `daily_totals(entries, start_date=None, end_date=None) -> list[dict]` | 返回按日期升序排列的每日收支序列，日期范围内无记录的日期自动补 `0`；未指定起止日期时自动取匹配记录的首末日期 |
| `monthly_comparison(entries, today=None) -> dict` | 独立使用全账本数据计算「本月至今」与「上月同期」支出对比，上月无对应日时自动对齐至上月月末（如 3 月 31 日对齐 2 月 28/29 日） |
| `format_cents(cents: int) -> str` | 纯整数整除与取模将整数分格式化为两位小数元字符串（如 `51500 -> "515.00"`），全程不经过 `float` |

### 2. `lotterytally.stats`

| 路由 / 接口 | 职责说明 |
| :--- | :--- |
| `GET /stats` | 解析查询参数（调用 `lotterytally.filters.parse_filters`）→ 过滤与排序（`filter_entries` / `sort_entries`）→ 计算 `summarize`、`daily_totals` 与 `monthly_comparison` → 渲染 `stats.html` 并向前端图表注入 `chart_data` JSON |
| `GET /stats/export.csv` | 与 `/stats` 共用完全一致的 `parse_filters -> filter_entries -> sort_entries` 链路（不分页截断），调用 `lotterytally.csv_export.export_csv` 生成唯一 UTF-8 BOM (`EF BB BF`) 的 CSV 下载响应 |
| `stats_bp` / `register_stats_routes(app)` | 提供 Flask Blueprint 与幂等注册函数，应用入口 `app.py` 直接调用 `app.register_blueprint(stats_bp)` 或 `register_stats_routes(app)` 即可挂载 |

---

## 二、上下游联调测试验证

在仓库根目录运行：

```powershell
python -m unittest discover -s test -v
```

测试覆盖方案验收项：
- **T01 / 空状态**：空账本或无匹配结果时，统计卡片显示 `0.00`，分类表与图表显示空状态提示，CSV 导出仅含表头且包含单份 UTF-8 BOM。
- **T05 & T11**：组合筛选（本月、饮食类、金额 30–50 元）包含上下界，当匹配记录超过单页 20 条（测试构造 26 条）时，统计汇总与 CSV 导出均包含完整 26 条且金额加总严格相等。
- **T07**：本月工资 `3000.00` 元，饮食 `470.00` 元 + `45.00` 元，统计页准确输出总支出 `515.00` 元、结余 `2485.00` 元。
- **T12**：已停用类别（如 `retired_snack` 夜宵）在统计页下拉框与汇总中仍可正常筛选和展示。
