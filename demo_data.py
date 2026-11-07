"""Command-line entry point for generating fictional demo data."""

from lotterytally.demo_data import build_demo_ledger, main, write_demo_ledger

__all__ = ["build_demo_ledger", "main", "write_demo_ledger"]


if __name__ == "__main__":
    raise SystemExit(main())
