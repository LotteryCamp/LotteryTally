"""PocketLedger local Flask application entry point."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from flask import Flask, render_template

LOGGER = logging.getLogger(__name__)

# These modules belong to other owners. Registration stays in this one place.
BLUEPRINTS = (
    ("lotterytally.entry_views", "entry_bp"),
    ("lotterytally.history", "history_bp"),
    ("lotterytally.stats", "stats_bp"),
    ("lotterytally.categories", "categories_bp"),
    ("lotterytally.budget", "budget_bp"),
    ("lotterytally.backup", "backup_bp"),
)

NAV_ITEMS = (
    ("首页", "/"),
    ("记一笔", "/entries/new"),
    ("流水", "/entries"),
    ("统计", "/stats"),
    ("预算", "/budget"),
    ("类别", "/categories"),
    ("备份", "/backup"),
)


def _default_ledger_path() -> Path:
    """Return the configured ledger path, defaulting to the repository data directory."""
    override = os.environ.get("LEDGER_DATA_PATH")
    if override:
        return Path(override).expanduser()

    return Path(__file__).resolve().parents[1] / "data" / "ledger.json"


def _register_available_blueprints(app: Flask) -> None:
    for module_name, blueprint_name in BLUEPRINTS:
        if importlib.util.find_spec(module_name) is None:
            LOGGER.warning("Skipping %s: module is not in the working tree yet", module_name)
            continue
        module = importlib.import_module(module_name)
        app.register_blueprint(getattr(module, blueprint_name))


def _home_data(app: Flask) -> dict[str, Any]:
    """Load home data through the shared storage and summary contracts."""
    if importlib.util.find_spec("lotterytally.summary") is None:
        return {"integration_ready": False, "transactions": [], "stats": {}}

    summary = importlib.import_module("lotterytally.summary")
    data_path = Path(app.config["LEDGER_DATA_PATH"])
    if importlib.util.find_spec("lotterytally.storage") is not None:
        storage = importlib.import_module("lotterytally.storage")
        ledger = storage.load_ledger(data_path)
    elif data_path.is_file():
        ledger = json.loads(data_path.read_text(encoding="utf-8"))
    else:
        ledger = {"entries": [], "categories": []}

    transactions = ledger["entries"]
    ordered = sorted(
        transactions,
        key=lambda item: (item["date"], item.get("created_at", ""), item["id"]),
        reverse=True,
    )
    category_names = {
        category["id"]: category["name"] for category in ledger.get("categories", [])
    }
    return {
        "integration_ready": True,
        "transactions": ordered[:10],
        "stats": summary.summarize(transactions, ledger.get("categories", [])),
        "category_names": category_names,
    }


def _format_money(cents: int | None) -> str:
    value = int(cents or 0)
    sign = "-" if value < 0 else ""
    dollars, remainder = divmod(abs(value), 100)
    return f"{sign}{dollars:,}.{remainder:02d}"


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    package_dir = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(package_dir / "templates"),
        static_folder=str(package_dir / "static"),
        static_url_path="/static",
    )
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("POCKETLEDGER_SECRET_KEY") or secrets.token_hex(32),
        LEDGER_DATA_PATH=str(_default_ledger_path()),
        DATA_FILE=str(_default_ledger_path()),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        TESTING=False,
    )
    if test_config:
        app.config.update(test_config)

    app.jinja_env.filters["money"] = _format_money
    _register_available_blueprints(app)

    @app.get("/")
    def home():
        data = _home_data(app)
        existing_paths = {rule.rule for rule in app.url_map.iter_rules()}
        navigation = [
            {"label": label, "path": path}
            for label, path in NAV_ITEMS
            if path == "/" or path in existing_paths
        ]
        return render_template("home.html", navigation=navigation, **data)

    @app.errorhandler(413)
    def payload_too_large(_error):
        return (
            render_template(
                "base.html",
                navigation=[{"label": "首页", "path": "/"}],
                page_title="文件过大",
                content_message="上传内容超过 2 MB 限制。",
            ),
            413,
        )

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 PocketLedger 本地记账应用")
    parser.add_argument(
        "--data-file",
        type=Path,
        default=_default_ledger_path(),
        help="账本 JSON 路径（默认 data/ledger.json）",
    )
    args = parser.parse_args()
    data_path = str(args.data_file.expanduser().resolve())
    app.config["LEDGER_DATA_PATH"] = data_path
    app.config["DATA_FILE"] = data_path
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
