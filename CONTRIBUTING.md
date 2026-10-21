# Contributing

Use Python 3.12+ and install development dependencies with `uv sync --extra dev --locked`.
Run `uv run ruff check .`, `uv run ruff format --check .`, and the full PostgreSQL test suite
documented in the README. Add tests for behavior changes, especially source parsing and delivery
state transitions. Use `.env` for local credentials and never commit production data or Telegram
updates. Label simulated source fixtures clearly. Preserve original Turkish notice text.

Changes to İETT endpoints belong in the source adapter. Keep time comparisons timezone-aware,
retain queued alerts after source deletion, and never infer resumed service from missing data.
Schema changes require a new numbered SQL migration; do not modify an applied migration.

Please describe the user-visible change and relevant validation in your pull request.
