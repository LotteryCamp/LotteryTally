"""Shared navigation entries for every page using the application shell."""

from flask import current_app


NAV_ITEMS = (
    ("首页", "/"),
    ("记一笔", "/entries/new"),
    ("流水", "/entries"),
    ("统计", "/stats"),
    ("预算", "/budget"),
    ("类别", "/categories"),
    ("备份", "/backup"),
)


def navigation_items():
    """Return the canonical links supported by the current Flask app."""
    paths = {rule.rule for rule in current_app.url_map.iter_rules()}
    return [
        {"label": label, "path": path}
        for label, path in NAV_ITEMS
        if path in paths
    ]
