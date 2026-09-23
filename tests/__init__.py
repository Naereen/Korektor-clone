"""Test package for Korektor's application, database, and workflow behavior.

The package groups unit tests for prompt and database helpers with Flask route
tests and temporary-project integration scenarios.  Tests are intentionally
isolated from live LLM providers: network-facing workflows use deterministic
mock responses, while the real application modules and SQLite schema remain
under test.  The suite is normally executed with ``uv run pytest tests`` or
through ``make test``.
"""
