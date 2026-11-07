"""Command-line entry point; application code lives in src/lotterytally."""

from lotterytally.app import app, create_app, main

__all__ = ["app", "create_app", "main"]


if __name__ == "__main__":
    main()
