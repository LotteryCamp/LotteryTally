"""Read-only ledger history and the home page's recent entries."""

from datetime import date, datetime

from flask import Blueprint, current_app, render_template, request

from .categories import list_categories
from .filters import (
    FilterValidationError,
    filter_entries,
    filters_to_query,
    parse_filters,
    sort_entries,
)


PAGE_SIZE = 20


def recent_entries(entries):
    """Return the latest ten entries from an already loaded ledger snapshot."""
    return sort_entries(entries)[:10]


def create_history_blueprint(
    load_ledger,
    list_categories,
    *,
    new_entry_endpoint=None,
    edit_entry_endpoint=None,
    delete_entry_endpoint=None,
    copy_enabled=False,
):
    """Register history with the shared ledger and category services.

    Entry form endpoints are supplied as they become available. Edit and delete
    endpoints accept the route argument ``id``. The new endpoint handles
    ``copy_from`` when copying is enabled.
    """
    history = Blueprint("history", __name__, template_folder="templates")

    @history.get("/entries")
    def show_entries():
        ledger = load_ledger()
        categories = list_categories(ledger, active_only=False)
        context = {
            "categories": categories,
            "category_names": {category["id"]: category["name"] for category in categories},
            "new_entry_endpoint": new_entry_endpoint,
            "edit_entry_endpoint": edit_entry_endpoint,
            "delete_entry_endpoint": delete_entry_endpoint,
            "copy_enabled": copy_enabled,
            "has_entries": bool(ledger["entries"]),
        }
        from lotterytally.navigation import navigation_items

        context["navigation"] = navigation_items()

        configured_today = current_app.config.get("LEDGER_TODAY")
        if type(configured_today) is date:
            today = configured_today
        elif isinstance(configured_today, str):
            today = date.fromisoformat(configured_today)
        else:
            today = datetime.now().astimezone().date()
        try:
            criteria = parse_filters(request.args, today)
        except FilterValidationError as error:
            return render_template(
                "history.html", **context, form=request.args, errors=error.errors
            ), 400

        matched = filter_entries(ledger["entries"], criteria)
        ordered = sort_entries(matched, criteria["sort"])
        page_count = max(1, (len(ordered) + PAGE_SIZE - 1) // PAGE_SIZE)
        requested_page = request.args.get("page", 1, type=int)
        page = min(max(1, requested_page), page_count)
        offset = (page - 1) * PAGE_SIZE

        query = filters_to_query(criteria)
        form = dict(query)
        if criteria["preset"]:
            # Only explicit dates should be submitted alongside a selected preset.
            form.update(start_date="", end_date="", preset=criteria["preset"])

        return render_template(
            "history.html",
            **context,
            form=form,
            errors={},
            entries=ordered[offset:offset + PAGE_SIZE],
            matched_count=len(ordered),
            page=page,
            page_count=page_count,
            query=query,
        )

    return history


def _load_app_ledger():
    """Resolve the ledger per request so CLI paths and separate apps stay isolated."""
    from .storage import load_ledger

    path = current_app.config.get("LEDGER_DATA_PATH") or current_app.config.get("DATA_FILE")
    return load_ledger(path)


history_bp = create_history_blueprint(_load_app_ledger, list_categories)
