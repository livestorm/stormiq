"""The v1 session record: pure functions over the payloads in `session_cache`.

- `clock.py`: the session clock (seconds since `started_at`) and minute bins.
- `evidence.py`: chat, questions and written answers as events on that clock,
  with stable ids (`C0001`, `Q001`, `A001`).

No database, network or web framework here. The classic workspace does not
import this package. See docs/architecture.md.
"""
