"""PocketLedger 统计页与 CSV 导出路由模块（王智钦模块）。

负责人：王智钦 102401609
对应《PocketLedger 现场编程与技术实施方案》第二节、第五节与第六节：
- GET /stats：解析筛选参数 → 过滤账目 → 计算收支汇总、分类表、每日趋势与月度对比 → 渲染 stats.html
- GET /stats/export.csv：复用完全一致的筛选与排序逻辑，调用 csv_export 生成带 UTF-8 BOM 的 CSV 下载响应

对外提供：
- stats_bp：Flask Blueprint（推荐通过 app.register_blueprint(stats_bp) 挂载）
- register_stats_routes(app)：幂等路由注册函数（兼容直接传入 Flask app）
- create_app(ledger_loader=None, today=None)：独立自测/演示用最小 Flask 应用工厂
"""

from __future__ import annotations

import csv
import importlib
import io
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from flask import Blueprint, Flask, Response, current_app, render_template, request
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

from .summary import (
    SummaryValidationError,
    daily_totals,
    format_cents,
    monthly_comparison,
    summarize,
)

from .filters import (
    FilterValidationError,
    filter_entries,
    filters_to_query,
    parse_filters,
    sort_entries,
)


# 优先导入蔡信坡的 CSV 导出模块（lotterytally.csv_export）；若主分支尚未合入则使用等价后备实现
try:
    from .csv_export import ExportError, export_csv, suggest_filename
except ModuleNotFoundError:
    class ExportError(ValueError):  # type: ignore[no-redef]
        """CSV 导出异常（后备实现）。"""

    _FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")

    def _safe_cell(val: Any) -> str:
        text = "" if val is None else str(val).strip()
        if text and text[0] in _FORMULA_PREFIX:
            return "'" + text
        return text

    def export_csv(
        entries: Iterable[Mapping[str, Any]] | None,
        categories: Iterable[Mapping[str, Any]] | None,
        sort: bool = False,
    ) -> str:
        cat_map = {
            str(c.get("id")): str(c.get("name") or c.get("id"))
            for c in (categories or [])
            if isinstance(c, Mapping) and c.get("id")
        }
        rows = list(entries or [])
        if sort:
            rows.sort(
                key=lambda r: (
                    str(r.get("date") or ""),
                    str(r.get("created_at") or ""),
                    str(r.get("id") or ""),
                ),
                reverse=True,
            )
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\r\n")
        writer.writerow(["日期", "收支类型", "类别", "金额（元）", "备注"])
        for row in rows:
            cid = str(row.get("category_id") or "")
            cname = cat_map.get(cid) or cid or "未分类"
            cents = int(row["amount_cents"])
            writer.writerow([
                _safe_cell(row.get("date")),
                "收入" if row.get("type") == "income" else "支出",
                _safe_cell(cname),
                f"{cents // 100}.{cents % 100:02d}",
                _safe_cell(row.get("note")),
            ])
        return "\ufeff" + buf.getvalue()

    def suggest_filename(prefix: str = "pocketledger") -> str:
        return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"


__all__ = [
    "stats_bp",
    "register_stats_routes",
    "create_app",
]

DEFAULT_CATEGORIES: list[dict[str, Any]] = [
    {"id": "food", "name": "饮食", "type": "expense", "active": True},
    {"id": "traffic", "name": "交通", "type": "expense", "active": True},
    {"id": "study", "name": "学习", "type": "expense", "active": True},
    {"id": "life", "name": "生活", "type": "expense", "active": True},
    {"id": "entertainment", "name": "娱乐", "type": "expense", "active": True},
    {"id": "medical", "name": "医疗", "type": "expense", "active": True},
    {"id": "other_expense", "name": "其他", "type": "expense", "active": True},
    {"id": "salary", "name": "工资", "type": "income", "active": True},
    {"id": "scholarship", "name": "奖学金", "type": "income", "active": True},
    {"id": "parttime", "name": "兼职", "type": "income", "active": True},
    {"id": "other_income", "name": "其他", "type": "income", "active": True},
]

_NAV_ITEMS = (
    ("首页", "/"),
    ("记一笔", "/entries/new"),
    ("流水", "/entries"),
    ("统计", "/stats"),
    ("预算", "/budget"),
    ("类别", "/categories"),
    ("备份", "/backup"),
)

stats_bp = Blueprint("stats", __name__, template_folder="templates")


def _resolve_today() -> date:
    """获取当前账本基准日期（支持测试通过 app.config['LEDGER_TODAY'] 注入固定日期）。"""
    configured = current_app.config.get("LEDGER_TODAY")
    if isinstance(configured, date) and not isinstance(configured, datetime):
        return configured
    if isinstance(configured, str):
        return date.fromisoformat(configured)
    return datetime.now().astimezone().date()


def _build_navigation() -> list[dict[str, str]]:
    """构建与 app.py / base.html 兼容的顶部导航栏链接列表（仅包含已注册路由）。"""
    existing_paths = {rule.rule for rule in current_app.url_map.iter_rules()}
    return [
        {"label": label, "path": path}
        for label, path in _NAV_ITEMS
        if path in existing_paths
    ]


def _load_ledger_snapshot() -> dict[str, Any]:
    """统一加载账本快照，自动适配队友的 storage.load_ledger 或测试注入的数据源。"""
    custom_loader: Callable[[], Mapping[str, Any]] | None = current_app.config.get("LEDGER_LOADER")
    if callable(custom_loader):
        data = custom_loader()
        return _coerce_ledger_dict(data)

    if "LEDGER_DATA" in current_app.config and isinstance(
        current_app.config["LEDGER_DATA"], Mapping
    ):
        return _coerce_ledger_dict(current_app.config["LEDGER_DATA"])

    data_file = (
        current_app.config.get("LEDGER_DATA_PATH")
        or current_app.config.get("DATA_FILE")
        or current_app.config.get("LEDGER_PATH")
        or os.environ.get("LEDGER_DATA_PATH")
    )

    try:
        storage = importlib.import_module(".storage", __package__)
    except ModuleNotFoundError as error:
        if error.name != f"{__package__}.storage":
            raise
    else:
        load_fn = getattr(storage, "load_ledger", None)
        if callable(load_fn):
            loaded = load_fn(data_file) if data_file else load_fn()
            return _coerce_ledger_dict(loaded)

    default_path = Path(__file__).resolve().parents[1] / "data" / "ledger.json"
    path = Path(data_file) if data_file else default_path
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _coerce_ledger_dict(raw)

    return {
        "schema_version": 1,
        "categories": list(DEFAULT_CATEGORIES),
        "entries": [],
        "budgets": {},
    }


def _coerce_ledger_dict(raw: Any) -> dict[str, Any]:
    """将 dict 或 LedgerData 对象统一规范为包含 entries 和 categories 的字典。"""
    if isinstance(raw, Mapping):
        entries = raw.get("entries")
        if entries is None:
            entries = raw.get("transactions", [])
        categories = raw.get("categories")
        budgets = raw.get("budgets") or {}
    else:
        entries = getattr(raw, "entries", None)
        if entries is None:
            entries = getattr(raw, "transactions", [])
        categories = getattr(raw, "categories", None)
        budgets = getattr(raw, "budgets", None) or {}

    normalized_entries: list[dict[str, Any]] = []
    for item in entries or []:
        if isinstance(item, Mapping):
            entry_dict = dict(item)
        elif hasattr(item, "to_dict") and callable(item.to_dict):
            entry_dict = dict(item.to_dict())
        else:
            entry_dict = {
                "id": getattr(item, "id", ""),
                "date": getattr(item, "date", ""),
                "type": getattr(item, "type", None) or getattr(item, "kind", ""),
                "amount_cents": getattr(item, "amount_cents", None)
                or getattr(item, "amount_fen", 0),
                "category_id": getattr(item, "category_id", None)
                or getattr(item, "category", ""),
                "note": getattr(item, "note", ""),
                "created_at": getattr(item, "created_at", ""),
            }
        # 确保 created_at 包含时区，满足 filters.sort_entries 的时区比较要求
        created_at = str(entry_dict.get("created_at") or "")
        if not created_at:
            entry_dict["created_at"] = f"{entry_dict.get('date', '2026-11-07')}T00:00:00+08:00"
        elif (
            "T" in created_at
            and not created_at.endswith("Z")
            and "+" not in created_at[10:]
            and "-" not in created_at[10:]
        ):
            entry_dict["created_at"] = created_at + "+08:00"
        normalized_entries.append(entry_dict)

    if not categories:
        try:
            from . import categories as cat_mod  # type: ignore[attr-defined]

            list_fn = getattr(cat_mod, "list_categories", None)
            if callable(list_fn):
                categories = list_fn(raw, active_only=False)
            if not categories:
                default_fn = getattr(cat_mod, "get_default_categories", None)
                if callable(default_fn):
                    categories = default_fn()
        except Exception:
            categories = None

    normalized_categories: list[dict[str, Any]] = []
    for cat in categories or DEFAULT_CATEGORIES:
        if isinstance(cat, Mapping):
            normalized_categories.append(dict(cat))
        else:
            normalized_categories.append({
                "id": getattr(cat, "id", ""),
                "name": getattr(cat, "name", ""),
                "type": getattr(cat, "type", "expense"),
                "active": getattr(cat, "active", True),
            })

    return {
        "schema_version": 1,
        "categories": normalized_categories,
        "entries": normalized_entries,
        "budgets": dict(budgets),
    }


def _empty_criteria() -> dict[str, Any]:
    return {
        "start_date": None,
        "end_date": None,
        "category_id": None,
        "type": None,
        "min_amount_cents": None,
        "max_amount_cents": None,
        "q": "",
        "sort": "date_desc",
        "preset": "",
    }


def _form_state_from_args(
    args: Mapping[str, Any], criteria: Mapping[str, Any], *, preserve_input: bool = False
) -> dict[str, str]:
    """生成用于筛选表单回显的字典（优先显示生效条件或用户输入值）。"""
    return {
        "start_date": str(args.get("start_date") or criteria.get("start_date") or "").strip(),
        "end_date": str(args.get("end_date") or criteria.get("end_date") or "").strip(),
        "category_id": str(args.get("category_id") or criteria.get("category_id") or "").strip(),
        "type": str(args.get("type") or criteria.get("type") or "").strip(),
        "min_amount": str(args.get("min_amount") or "").strip(),
        "max_amount": str(args.get("max_amount") or "").strip(),
        "q": str(args.get("q") or criteria.get("q") or "").strip(),
        "sort": str(args.get("sort") or criteria.get("sort") or "date_desc").strip(),
        "preset": str(
            (args.get("preset") if preserve_input else criteria.get("preset")) or ""
        ).strip(),
    }


@stats_bp.route("/stats", methods=["GET"])
def stats_page() -> tuple[str, int] | str:
    """统计卡片、分类表、月度对比和可视化图表页面（GET /stats）。"""
    today = _resolve_today()
    ledger = _load_ledger_snapshot()
    all_entries = ledger["entries"]
    categories = ledger["categories"]

    errors: dict[str, str] = {}
    status_code = 200

    try:
        criteria = parse_filters(request.args, today)
        matched = filter_entries(all_entries, criteria)
        ordered = sort_entries(matched, criteria["sort"])
        query_params = filters_to_query(criteria)
    except FilterValidationError as exc:
        errors = dict(exc.errors)
        criteria = _empty_criteria()
        matched = []
        ordered = []
        query_params = {}
        status_code = 400

    summary_data = summarize(matched, categories)
    daily_data = daily_totals(
        matched,
        start_date=criteria.get("start_date"),
        end_date=criteria.get("end_date"),
    )
    comparison_data = monthly_comparison(all_entries, today=today)

    chart_data = {
        "category_expenses": summary_data["by_category"],
        "by_category": summary_data["by_category"],
        "daily_totals": daily_data,
        "daily_series": daily_data,
        "income_cents": summary_data["income_cents"],
        "expense_cents": summary_data["expense_cents"],
        "balance_cents": summary_data["balance_cents"],
        "count": summary_data["count"],
    }

    form_values = _form_state_from_args(request.args, criteria, preserve_input=bool(errors))

    html = render_template(
        "stats.html",
        navigation=_build_navigation(),
        summary=summary_data,
        daily_totals=daily_data,
        daily_series=daily_data,
        monthly_comparison=comparison_data,
        chart_data=chart_data,
        categories=categories,
        criteria=criteria,
        form=form_values,
        errors=errors,
        query_params=query_params,
        entries=ordered,
        today=today.isoformat(),
        format_cents=format_cents,
    )
    return html, status_code


@stats_bp.route("/stats/export.csv", methods=["GET"])
def export_stats_csv() -> Response | tuple[str, int]:
    """按当前筛选条件导出全部匹配账目的 CSV（GET /stats/export.csv）。

    严格与 /stats 共享同一套 parse_filters -> filter_entries -> sort_entries 逻辑，
    不截断分页，保证导出笔数与金额合计同统计页完全一致（方案 T11）。
    """
    today = _resolve_today()
    ledger = _load_ledger_snapshot()
    all_entries = ledger["entries"]
    categories = ledger["categories"]

    try:
        criteria = parse_filters(request.args, today)
    except FilterValidationError as exc:
        msg = "；".join(exc.errors.values())
        return f"筛选参数不合法，无法导出 CSV：{msg}", 400

    matched = filter_entries(all_entries, criteria)
    ordered = sort_entries(matched, criteria["sort"])

    try:
        csv_text = export_csv(ordered, categories, sort=False)
    except (ExportError, SummaryValidationError, ValueError) as exc:
        return f"导出 CSV 失败：{exc}", 400

    filename = suggest_filename("pocketledger_stats")
    # csv_export.export_csv 返回的字符串已以 '\ufeff' (UTF-8 BOM) 开头，
    # 此处直接按 utf-8 编码即可得到唯一的 EF BB BF 字节序标记，切勿重复添加 BOM。
    if not csv_text.startswith("\ufeff"):
        csv_text = "\ufeff" + csv_text

    return Response(
        csv_text.encode("utf-8"),
        status=200,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


_FALLBACK_BASE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}PocketLedger 口袋账本{% endblock %}</title>
</head>
<body>
  <nav style="background:#1e293b;color:#fff;padding:12px 24px;display:flex;gap:20px;align-items:center;">
    <strong style="font-size:18px;">PocketLedger 口袋账本</strong>
    <a href="/" style="color:#e2e8f0;text-decoration:none;">首页</a>
    <a href="/entries/new" style="color:#e2e8f0;text-decoration:none;">记一笔</a>
    <a href="/entries" style="color:#e2e8f0;text-decoration:none;">流水查询</a>
    <a href="/stats" style="color:#38bdf8;text-decoration:none;font-weight:bold;">收支统计</a>
    <a href="/budget" style="color:#e2e8f0;text-decoration:none;">预算管理</a>
    <a href="/categories" style="color:#e2e8f0;text-decoration:none;">类别管理</a>
    <a href="/backup" style="color:#e2e8f0;text-decoration:none;">备份恢复</a>
  </nav>
  <main style="max-width:1100px;margin:24px auto;padding:0 16px;">
    {% block content %}{% endblock %}
  </main>
</body>
</html>
"""


def _ensure_fallback_base_template(app: Flask) -> None:
    """支持同时从仓库根目录 templates/、包内 lotterytally/templates/ 查找模板，并提供 base.html 兜底。"""
    if getattr(app, "_pocketledger_stats_loader_configured", False):
        return
    repo_root_templates = Path(__file__).resolve().parent.parent / "templates"
    pkg_templates = Path(__file__).resolve().parent / "templates"
    loaders: list[Any] = []
    if app.jinja_loader is not None:
        loaders.append(app.jinja_loader)
    loaders.append(FileSystemLoader([str(repo_root_templates), str(pkg_templates)]))
    loaders.append(DictLoader({"base.html": _FALLBACK_BASE_HTML}))
    app.jinja_loader = ChoiceLoader(loaders)
    setattr(app, "_pocketledger_stats_loader_configured", True)


@stats_bp.record_once
def _on_blueprint_registered(state: Any) -> None:
    _ensure_fallback_base_template(state.app)


def register_stats_routes(app: Flask) -> None:
    """将统计与导出路由幂等注册到 Flask 应用实例。"""
    _ensure_fallback_base_template(app)
    if "stats" not in app.blueprints:
        app.register_blueprint(stats_bp)


def create_app(
    ledger_loader: Callable[[], Mapping[str, Any]] | None = None,
    today: date | str | None = None,
) -> Flask:
    """创建可独立运行的最小 Flask 应用，供统计模块自测与集成验证。"""
    app = Flask(__name__, template_folder="templates")
    if ledger_loader is not None:
        app.config["LEDGER_LOADER"] = ledger_loader
    if today is not None:
        app.config["LEDGER_TODAY"] = today
    register_stats_routes(app)
    return app
