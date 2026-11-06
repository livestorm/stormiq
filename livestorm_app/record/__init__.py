"""The v1 session record: pure functions over the payloads in `session_cache`.

- `clock.py`: the session clock (seconds since `started_at`) and minute bins.
- `evidence.py`: chat, questions and written answers as events on that clock,
  with stable ids (`C0001`, `Q001`, `A001`).
- `roster.py`: one row per person, with a role, a channel, a lead time and a
  ref (`P-…`); the only views of people a prompt or a page may get.
- `authors.py`: chat, question and answer authors resolved to roster people
  by people id, with honest coverage (an author never resolves by guess).
- `transcript.py`: the stored Gladia transcript as utterances with stable
  ids (`U0001`) and their words.
- `transcript_health.py`: how far the transcript can be trusted (word
  confidence, speech coverage, speakers, language, unclear stretches).
- `inputs.py`: the single loader of a session's record inputs (payloads,
  clock, roster, authors, the parsed transcript and its health).
- `measures.py`: the measures registry: every number the Report shows, with
  its numerator, denominator, state, coverage and exclusions.
- `assemble.py`: the record as `GET /api/sessions/{id}/record` serves it
  (the clock, the roster header, the Pulse, the transcript's health, the
  measures).

No database, network or web framework here. The classic app does not
import this package: only `api_logic.get_session_record`, the v1 record
route's loader, imports it, inside the function. See docs/architecture.md.
"""
