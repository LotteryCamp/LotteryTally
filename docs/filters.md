# 杜玉鹤：筛选模块交付与接入说明

对应《PocketLedger 现场编程与技术实施方案》第二节，负责人杜玉鹤（162404109），业务文件为 `lotterytally/filters.py`，独立验收文件位于 `test/`。

## 范围与运行

已实现日期、类别 ID、收支类型、金额区间的 AND 组合筛选，以及今天、本周、本月、上月快捷日期、备注子串搜索、日期和金额排序。模块只接收统一模型的内存账目，不读写账本；返回全部匹配结果，分页由流水模块负责。

源码统一放入 `lotterytally/` 包，项目信息、Python 版本及 Flask 应用依赖统一维护在根目录的 `pyproject.toml`。筛选模块自身仅使用标准库。当前实际验证环境为 Windows PowerShell、Python 3.14.5；从仓库根目录执行：

```powershell
python -m pip install -e .
python -m unittest discover -s test -v
```

筛选行为测试位于 `test/test_filters.py`，包内共享校验导入的回归测试位于 `test/test_package_imports.py`。

2026-11-07 实际运行结果：43 项测试全部通过，包含原有 40 项筛选测试和 3 项包导入回归测试。

同日已验证 editable 安装、wheel 构建与安装、依赖完整性及仓库外导入。wheel 只包含 `lotterytally` 源码包及分发元数据；从仓库外针对已安装 wheel 运行同一套 43 项测试，全部通过。

流水、统计、CSV 路由以及完整 Flask 应用由相应负责人接入后再验收。本次交付由 Codex 辅助实现、复核并执行自动化测试，提交使用当前连接的 GitHub 账号身份。

## 接口约定

方案锁定了三个函数名，以下明确其返回结构和错误处理，供相邻模块共用。

| 接口 | 返回值与行为 |
| --- | --- |
| `parse_filters(query, today)` | 返回 `FilterCriteria` 字典；非法输入抛出 `FilterValidationError`，其 `errors` 是字段名到中文提示的字典 |
| `filter_entries(entries, criteria)` | 返回新的完整匹配列表，保留输入顺序；不修改账目，不分页 |
| `sort_entries(entries, order="date_desc")` | 返回新的排序列表；不修改输入 |
| `filters_to_query(criteria)` | 返回生效 GET 参数字典，供翻页、统计与 CSV 链接共用 |

`query` 支持普通字典和 Flask 的 `request.args`。已知参数必须是文本，首尾空白去除；空白视为未指定。`page` 和未知参数忽略。`today` 必须为 `datetime.date`，由路由传入本地账本日期，函数不读取系统时钟。

示例：

```python
from datetime import date
from lotterytally.filters import parse_filters

criteria = parse_filters({
    "preset": "this_month",
    "category_id": "food",
    "type": "expense",
    "min_amount": "30",
    "max_amount": "50",
    "sort": "amount_desc",
}, today=date(2026, 11, 7))

# criteria == {
#     "start_date": "2026-11-01", "end_date": "2026-11-30",
#     "category_id": "food", "type": "expense",
#     "min_amount_cents": 3000, "max_amount_cents": 5000,
#     "q": "", "sort": "amount_desc", "preset": "this_month",
# }
```

### 参数和边界

| GET 参数 | 规则 |
| --- | --- |
| `start_date`, `end_date` | 严格 `YYYY-MM-DD`，必须为实际日期，两端均包含；支持只填一端 |
| `category_id` | 按稳定 ID 精确匹配，停用类别历史记录仍可查；不根据类别显示名称匹配 |
| `type` | `income` 或 `expense` |
| `min_amount`, `max_amount` | 元文本转正整数分，两端均包含；如 `45`、`45.5`、`45.50` |
| `q` | 去除首尾空白后匹配备注普通子串，区分大小写；引号、百分号等不作为表达式 |
| `sort` | 默认 `date_desc`，可用 `date_asc`、`amount_asc`、`amount_desc` |
| `preset` | `today`、`this_week`、`this_month`、`last_month` |

筛选金额沿用方案的正金额规则：拒绝 `0`、负数、超过两位小数、科学计数法、NaN、Infinity；不填金额即不设该侧范围。共享 `lotterytally.validators.parse_amount_cents` 可用时通过包内相对导入直接复用，其契约为返回正整数分、非法金额抛出 `ValueError`。共享模块尚未合入时使用等价的字符串格式检查和 `Decimal` 后备转换，保证模块能独立验收；共享模块内部的其他依赖错误继续抛出，便于定位集成问题。不会使用浮点数计算金额。

起始日期晚于结束日期、最小金额大于最大金额都会报字段错误，避免页面静默显示误导性的空结果。独立字段错误一起报告。

### 快捷日期优先级

- 本周为周一至周日；本月、上月使用完整日历月，自动处理跨年和闰年。
- 任意一侧显式日期非空时，整个 `preset` 失效；未填写的另一侧保持开放。
- 显式日期非法时报告错误，不回退到快捷日期；未知快捷选项在其生效时报告错误。
- `filters_to_query` 将快捷日期固化为实际起止日期，删除 `preset` 和 `page`，避免跨页或导出时日期漂移。金额统一序列化为两位小数的元文本。

例如同时传入 `start_date=2026-10-15&preset=this_month`，生效范围为 `2026-10-15` 起至不限结束日期。

### 排序约定

默认按 `date`、`created_at`、`id` 全部倒序。`created_at` 按实际带时区时刻比较，支持不同 UTC 偏移和 `Z` 后缀。传入账目须符合统一模型，包含有效 ISO 日期、正整数分金额、带时区时间戳及唯一 ID。

金额升降序在金额相同时沿用默认顺序，保证分页稳定。日期升序只将账目日期改为升序，同日仍按录入时刻和 ID 倒序。

## 流水、统计与导出接入

以下为调用约定示例；加载账本、页面渲染、汇总和 CSV 文件输出仍使用对应模块的接口。

```python
from datetime import datetime
from lotterytally.filters import (
    FilterValidationError, parse_filters, filter_entries,
    sort_entries, filters_to_query,
)

# 路由统一捕获字段错误，并把 request.args 回填表单。
try:
    criteria = parse_filters(request.args, datetime.now().astimezone().date())
except FilterValidationError as exc:
    # render_template(..., errors=exc.errors, form=request.args)，返回 400。
    ...
else:
    matched = filter_entries(ledger["entries"], criteria)
    ordered = sort_entries(matched, criteria["sort"])
    query_params = filters_to_query(criteria)
    # 流水：最后才对 ordered 分页；url_for(..., **query_params, page=2)。
    # 统计与图表：使用完整 matched，禁止使用分页后的列表。
    # CSV：使用完整 ordered，url_for(..., **query_params)。
```

类别筛选下拉框应包含已停用类别。用户手动改日期时，页面清除 `preset`；服务端仍按上述优先级兜底。页面初次使用快捷选项后，可以保留 `criteria["preset"]` 展示当前选项，其他链接统一使用 `filters_to_query`。

## 验收对应关系

- 方案 T05：本月、饮食、30–50 元组合筛选，只返回同时满足条件的记录，包含 30 和 50 元。
- 方案 T06 的模块边界：规范化参数保留关键词、日期、金额和排序，流水负责人在生成分页链接时另加 `page`。
- 方案 T11 的模块边界：筛选结果不截断，统计和 CSV 可取得同一完整集合；页面和文件的最终联调由相应负责人完成。
- 停用类别继续按 ID 查询；输入集合和账目内容保持原样。
- 自动化测试覆盖非法输入、等值/单侧范围、跨年/闰月、显式日期优先、时区排序、同值排序、参数往返及大于一页的完整结果。
