"""The v1 session record: pure functions over the payloads in `session_cache`.

- `clock.py`: the session clock (seconds since `started_at`) and minute bins.
- `evidence.py`: chat, questions and written answers as events on that clock,
  with stable ids (`C0001`, `Q001`, `A001`).
- `roster.py`: one row per person, with a role, a channel, a lead time and a
  ref (`P-…`); the only views of people a prompt or a page may get.
- `inputs.py`: the single loader of a session's record inputs (payloads,
  clock, roster).

No database, network or web framework here. The classic workspace does not
import this package. See docs/architecture.md.
"""
