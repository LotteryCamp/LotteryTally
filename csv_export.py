"""
csv_export.py —— CSV 导出模块

负责人：蔡信坡 162404103
接口：export_csv(entries, categories) -> str

职责边界：
    只负责把已经筛选好的账目列表转成 CSV 文本，不读文件、不做筛选、不排版页面。
    调用方（统计路由）把筛选结果和类别表传进来，拿到的字符串直接作为下载响应体。

两个容易踩的坑，这里都处理了：
    1. Excel 打开 UTF-8 CSV 中文乱码 —— 输出以 BOM 开头。
    2. 备注以 = + - @ 开头时被表格软件当公式执行 —— 前面补一个单引号转成文本。
"""

import csv
import io
from datetime import datetime

# 表头。顺序按方案 6.2 节：日期、收支类型、类别、金额、备注
# 金额放第 3 列，和常见记账软件的习惯一致，也方便在 Excel 里直接求和
HEADER = ["日期", "收支类型", "类别", "金额（元）", "备注"]

# 表格软件会把这些开头的单元格当公式解析，需要转义
FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")

# 类别找不到时的兜底显示
UNKNOWN_CATEGORY = "未分类"


def _escape_cell(value):
    """把单元格内容转成安全文本。

    以公式触发符开头的，前面补单引号。这是 OWASP 推荐的 CSV 注入防护做法，
    补完之后表格软件会按纯文本显示，不会去执行。
    """
    text = "" if value is None else str(value)
    # 只去首尾空白，中间的空格要保留（用户可能故意对齐）
    text = text.strip()
    if text and text[0] in FORMULA_PREFIX:
        return "'" + text
    return text


def cents_to_yuan(amount_cents):
    """整数分转成两位小数的元字符串，全程不经过 float。

    用整除和取余拼字符串，避免 0.1 + 0.2 这类浮点误差污染导出结果。
    负数（比如以后要导净支出）也能正确带上负号。
    """
    try:
        cents = int(amount_cents)
    except (TypeError, ValueError):
        return "0.00"

    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return "{}{}.{:02d}".format(sign, cents // 100, cents % 100)


def _category_map(categories):
    """把类别表转成 id -> 名称 的字典，导出时按 id 换成人类可读的名字。"""
    mapping = {}
    for item in categories or []:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if cid:
            mapping[cid] = item.get("name") or cid
    return mapping


def _sort_key(entry):
    """排序键：账目日期 > 录入时间 > id，全部倒序。

    和流水页「最近记录」用的是同一套口径（方案 6.2 节），
    这样页面看到的顺序和导出的顺序一致，演示时对得上。
    """
    return (
        entry.get("date") or "",
        entry.get("created_at") or "",
        str(entry.get("id") or ""),
    )


def export_csv(entries, categories, sort=True):
    """生成 CSV 文本。

    参数：
        entries    —— 账目 dict 列表，字段见方案 4.1 节（amount_cents 为整数分）
        categories —— 类别 dict 列表，用于把 category_id 换成名称
        sort       —— 是否按页面口径倒序排列。默认 True；
                      如果调用方已经排好序且不想被打乱，传 False

    返回：
        带 UTF-8 BOM 的 CSV 字符串。空列表时返回只有表头的文件，
        保证前端「导出」按钮在任何筛选条件下都能下载到东西。
    """
    rows = list(entries or [])
    if sort:
        rows.sort(key=_sort_key, reverse=True)

    cat_map = _category_map(categories)

    buf = io.StringIO()
    # 换行用 \r\n，Excel 和 WPS 都能正确识别
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(HEADER)

    for entry in rows:
        if not isinstance(entry, dict):
            continue

        cid = entry.get("category_id") or ""
        # 类别被删除或 ID 写错时退回到原始 ID，宁可难看也不要丢掉这条账的信息
        category_name = cat_map.get(cid) or cid or UNKNOWN_CATEGORY

        writer.writerow([
            _escape_cell(entry.get("date")),
            "收入" if entry.get("type") == "income" else "支出",
            _escape_cell(category_name),
            cents_to_yuan(entry.get("amount_cents")),
            _escape_cell(entry.get("note")),
        ])

    # \ufeff 是 BOM，Flask 用 utf-8 编码后正好是 EF BB BF，Excel 才认得出是 UTF-8
    return "\ufeff" + buf.getvalue()


def suggest_filename(prefix="pocketledger"):
    """生成下载文件名，带时间戳避免多次导出互相覆盖。

    文件名只用 ASCII：中文文件名在部分浏览器和 Windows 压缩包里会变乱码，
    这一步求稳。
    """
    return "{}_{}.csv".format(prefix, datetime.now().strftime("%Y%m%d_%H%M%S"))
