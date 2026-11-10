"""The people sync: a session's complete people list, read from Livestorm in the worker.

Livestorm lists a session's people 50 per page (`client.fetch_session_people`),
about one call per 50 registrants, so the list is read only by the worker job
`worker.run_people_sync`, never in a web request. It is saved whole as the
session's roster (`db.replace_session_roster`). docs/jobs.md has the flow.

Web side (the routes in `routes/sessions.py`):
- `read_roster_status`: the roster header and the latest sync, scoped by the
  caller's org; a sync whose queue job is gone is closed here (`_reconcile_sync`).
- `request_people_sync`: an explicit sync (`POST /roster-sync`), scope-checked
  first, then `start_people_sync`.
- `start_sync_on_fetch`: the automatic start after a session's base fetch
  (`api_logic.fetch_session_base_data`), through `start_people_sync` too.
- `roster_live_state`: the sync's state as the v1 record's roster header
  carries it (`api_logic.get_session_record`), read on every request.
- `start_people_sync`: the guards (same org, an active sync to attach to, the
  cooldown, the error backoff for automatic starts, the per-org daily call
  cap), then the `livestorm_sync_jobs` row and the arq job. The job's
  arguments are ids only: the caller's connection id (also their session
  cookie) waits on the row until the worker claims it.

Worker side (`worker.run_people_sync` calls them in a thread):
- `session_organization`: the session's stamped org, the key of the per-org lock.
- `check_daily_cap`: the daily cap again, under the org's lock, where it is exact.
- `_resolve_sync_credentials`: the connection's org must equal the session's
  org, or no Livestorm call is made.
- `run_people_sync`: people, then the workspace's field labels, then the
  roster. Any failure raises before the write, so the previous roster stays,
  and marks the row `error` first.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

import requests

from livestorm_app.auth import livestorm_auth_for_job, local_dev_enabled
from livestorm_app.db import (
    ALL_ORGS,
    OrgScope,
    create_livestorm_sync,
    fetch_oauth_connection,
    fetch_roster_status,
    fetch_session_fields,
    get_org_people_attributes,
    livestorm_sync_calls_since,
    livestorm_sync_is_first_initial,
    replace_session_roster,
    update_livestorm_sync,
    upsert_org_people_attributes,
)
from livestorm_app.livestorm import client as livestorm_client
from livestorm_app.livestorm.client import LivestormDeadlineExceeded, LivestormPaginationError
from livestorm_app.livestorm.payloads import parse_person
from livestorm_app.progress import publish_progress_sync, read_progress
from livestorm_app.queue import enqueue_unique_job_sync, livestorm_sync_job_id, read_job_status_sync


logger = logging.getLogger(__name__)

PROGRESS_KIND = "roster"
ACTIVE_SYNC_STATUSES = ("pending", "running")

# A session isn't synced again within this long of a sync that completed.
PEOPLE_SYNC_COOLDOWN_MINUTES = 15
# An automatic start doesn't retry within this long of a failed sync; only an
# explicit POST /roster-sync does.
PEOPLE_SYNC_ERROR_BACKOFF = timedelta(hours=1)
# A sync row whose arq job has vanished is closed after this grace period,
# which covers the moment between inserting the row and enqueueing its job.
SYNC_LOST_AFTER_SECONDS = 120
# The roster is due a refresh this long after the session ends (PR-20 runs it).
PEOPLE_REFRESH_DELAY_DAYS = 7
# The workspace's field labels are read again when older than this.
PEOPLE_ATTRIBUTES_MAX_AGE = timedelta(hours=24)

# The per-org daily cap on Livestorm calls, over a rolling 24 hours. Every
# sync counts except a session's first initial sync (owner decision, Q5).
DAILY_CALLS_ENV = "LIVESTORM_SYNC_DAILY_CALLS_PER_ORG"
DEFAULT_DAILY_CALLS_PER_ORG = 1000
DAILY_CALLS_WINDOW = timedelta(hours=24)

SKIPPED_OTHER_ORG = "other_org"
SKIPPED_COOLDOWN = "cooldown"
SKIPPED_ERROR_BACKOFF = "error_backoff"
SKIPPED_DAILY_CAP = "daily_cap"


class RosterSessionNotFound(LookupError):
    """The session isn't cached, or isn't in the caller's scope (404)."""


class PeopleSyncCapReached(RuntimeError):
    """The org reached its daily cap of Livestorm calls (429)."""

    def __init__(self, limit: int, used: int) -> None:
        super().__init__(
            "Your organization reached its daily limit of Livestorm calls for reading people. "
            "Try again tomorrow."
        )
        self.limit = limit
        self.used = used


class PeopleSyncUnavailable(RuntimeError):
    """The job queue couldn't take the sync (503)."""


class PeopleSyncStopped(RuntimeError):
    """The job was cancelled (time limit or worker shutdown); its thread stops."""


class PeopleSyncBusy(RuntimeError):
    """Another sync of the org already waits for the org's lock (worker side)."""

    def __init__(
        self,
        message: str = "Another people sync of your organization is already waiting to run. Please try again in a few minutes.",
    ) -> None:
        super().__init__(message)


class PeopleSyncFailed(RuntimeError):
    """What a failed sync raises to arq: `failure_message` only.

    arq pickles a failed job's exception into Redis as its result (kept for an
    hour), and a status read unpickles it in the web process. A `requests`
    exception carries its request, whose headers hold the user's token, and
    the upstream body, so the job never lets one reach arq (`worker.run_people_sync`).
    """


# Refusals by design: nothing was read, and they need no traceback in the logs.
EXPECTED_REFUSALS = (PeopleSyncBusy, PeopleSyncCapReached)


# ── Small helpers ───────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age(value: Any, now: datetime) -> Optional[timedelta]:
    if not isinstance(value, datetime):
        return None
    moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return now - moment


def _iso(value: Any) -> Optional[str]:
    if not isinstance(value, datetime):
        return None
    moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return moment.isoformat()


def daily_calls_cap() -> int:
    """`LIVESTORM_SYNC_DAILY_CALLS_PER_ORG`: the Livestorm calls one org's
    syncs may make in a rolling 24 hours. 1,000 when unset. An invalid value
    stops every sync the cap applies to until it is fixed (0)."""
    raw = (os.getenv(DAILY_CALLS_ENV) or "").strip()
    if not raw:
        return DEFAULT_DAILY_CALLS_PER_ORG
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        logger.error("%s must be a whole number of calls; people syncs under the cap are stopped until it is fixed.", DAILY_CALLS_ENV)
        return 0
    return value


def failure_message(exc: BaseException) -> str:
    """What a failed sync's row says: a short message of our own, never an
    upstream URL or body, a key, a token or a connection id."""
    kept = "The previous people list is kept."
    if isinstance(exc, PeopleSyncStopped):
        return "The people sync was interrupted (time limit reached or worker restarted). Please try again."
    if isinstance(exc, LivestormDeadlineExceeded):
        return f"Livestorm took too long to list every person. {kept} Please try again."
    if isinstance(exc, LivestormPaginationError):
        return f"Livestorm's people list never reached its last page. {kept} Please try again."
    if isinstance(exc, requests.HTTPError):
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code in (401, 403):
            return f"Livestorm refused to list this session's people (HTTP {status_code}). Please reconnect and try again."
        if status_code == 404:
            return "Livestorm couldn't find this session's people (HTTP 404)."
        if status_code == 429:
            return f"Livestorm's rate limit was reached. {kept} Please try again later."
        return f"Livestorm answered HTTP {status_code or 'error'} while listing the people. {kept} Please try again."
    if isinstance(exc, requests.RequestException):
        return f"Livestorm couldn't be reached while listing the people. {kept} Please try again."
    if isinstance(exc, (RuntimeError, ValueError)) and str(exc).strip():
        # StormIQ's own messages (credentials, database, the roster's checks).
        return str(exc).strip()[:300]
    return f"The people sync failed. {kept} Please try again."


def mark_sync_failed(sync_id: str, exc: BaseException, *, calls: Optional[int] = None) -> None:
    """Mark a sync `error` (its connection id cleared) with `failure_message`.
    Never raises: a failure here is logged, and the sweeper closes the row."""
    try:
        update_livestorm_sync(sync_id, status="error", error=failure_message(exc), pages_fetched=calls)
    except Exception:
        logger.exception("Could not mark people sync %s as failed", sync_id)


# ── Web side ────────────────────────────────────────────────────────────────


def _reconcile_sync(sync: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """Close an active sync whose arq job is gone, as `api_logic._reconcile_transcript_job`
    does for transcriptions.

    A session holds one active sync (unique index), so a dead row would block
    it, and keep its connection id, until the sweeper an hour later. arq
    reporting a failure closes the row at once; arq having no trace of the
    job closes it `SYNC_LOST_AFTER_SECONDS` after the row's last update. When
    Redis can't be reached the row is left alone. Only a row still active is
    closed, so a sync that finished meanwhile keeps its status.
    """
    if not sync or sync.get("status") not in ACTIVE_SYNC_STATUSES:
        return dict(sync) if sync else None
    arq_status = read_job_status_sync(livestorm_sync_job_id(sync["sync_id"]))
    state = arq_status.get("jobStatus")
    if state == "error":
        logger.warning("People sync %s failed in the queue", sync["sync_id"])
        message = "The people sync stopped before finishing. Please try again."
    elif state == "not_found" and (_age(sync.get("updated_at"), _now()) or timedelta(0)) > timedelta(seconds=SYNC_LOST_AFTER_SECONDS):
        message = "The people sync was lost by the queue (the worker probably restarted). Please try again."
    else:
        return dict(sync)
    if not update_livestorm_sync(sync["sync_id"], status="error", error=message, only_from=ACTIVE_SYNC_STATUSES):
        return dict(sync)
    return {**sync, "status": "error", "error": message}


def _roster_view(roster: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if not roster:
        return None
    return {
        "version": roster.get("roster_version"),
        "source": roster.get("source"),
        "complete": roster.get("complete"),
        "recordCount": roster.get("record_count"),
        "peopleCount": roster.get("people_count"),
        "fieldsSource": roster.get("fields_source"),
        "reason": roster.get("reason"),
        "syncedAt": _iso(roster.get("synced_at")),
        "refreshDueAt": _iso(roster.get("refresh_due_at")),
        "refreshedAt": _iso(roster.get("refreshed_at")),
    }


def _sync_view(sync: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if not sync:
        return None
    return {
        "syncId": sync.get("sync_id"),
        "reason": sync.get("reason"),
        "status": sync.get("status"),
        "error": sync.get("error"),
        "calls": sync.get("pages_fetched"),
        "recordCount": sync.get("record_count"),
        "peopleWritten": sync.get("people_written"),
        "createdAt": _iso(sync.get("created_at")),
        "updatedAt": _iso(sync.get("updated_at")),
    }


def roster_live_state(
    roster: Optional[Mapping[str, Any]], sync: Optional[Mapping[str, Any]], *, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """What the v1 record's roster header says about the people sync:
    `record.roster.sync` and `record.roster.refreshDue`.

    `roster` is the stored roster's header and `sync` the session's latest
    people sync, both as `db.fetch_roster_status` gives them (None when the
    session has none). They change without a new version of the session's
    row, so `api_logic.get_session_record` reads them on every request and
    never caches them.

    - `sync`: `status` (`none` without a sync), `active` (pending or
      running: the page polls `GET /roster-status` until it ends), `reason`,
      `error` (the sync's own message, `failure_message`, never an upstream
      one) and `updatedAt`. The status is the stored one: a sync whose
      queue job was lost is closed by `read_roster_status`, not here, so a
      record read never asks Redis.
    - `refreshDue`: the stored roster is due its refresh (`refresh_due_at`
      has passed). False without a stored roster.

    Never a person, a sync id, a connection id or a token.
    """
    sync = sync if isinstance(sync, Mapping) else {}
    roster = roster if isinstance(roster, Mapping) else {}
    status = str(sync.get("status") or "").strip() or "none"
    due = _age(roster.get("refresh_due_at"), now or _now())
    return {
        "sync": {
            "status": status,
            "active": status in ACTIVE_SYNC_STATUSES,
            "reason": sync.get("reason") or None,
            "error": sync.get("error") or None,
            "updatedAt": _iso(sync.get("updated_at")),
        },
        "refreshDue": due is not None and due >= timedelta(0),
    }


def read_roster_status(session_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """`GET /api/sessions/{id}/roster-status`: the roster header and the latest sync.

    `organization_id` is the caller's read scope; one scoped query
    (`db.fetch_roster_status`) is also the scope check, so a session out of
    scope raises `RosterSessionNotFound` before anything else. An active sync
    is checked against arq first (`_reconcile_sync`) and carries the Redis
    progress. Never a person, a connection id or a token.
    """
    session_id = str(session_id or "").strip()
    status = fetch_roster_status(session_id, organization_id)
    if status is None:
        raise RosterSessionNotFound()
    sync = _reconcile_sync(status.get("sync"))
    active = bool(sync) and sync.get("status") in ACTIVE_SYNC_STATUSES
    return {
        "sessionId": session_id,
        "status": sync.get("status") if sync else "none",
        "roster": _roster_view(status.get("roster")),
        "sync": _sync_view(sync),
        "progress": read_progress(PROGRESS_KIND, session_id) if active else None,
    }


def _start_result(sync: Optional[Mapping[str, Any]], *, started: bool, skipped: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    return {
        "status": sync.get("status") if sync else "none",
        "syncId": sync.get("sync_id") if sync else None,
        "started": started,
        "skipped": skipped,
        **extra,
    }


def start_people_sync(
    session_id: str,
    *,
    reason: str,
    caller_org_id: str,
    session_org_id: str,
    job_connection_id: str,
    roster_status: Mapping[str, Any],
    local_dev: bool = False,
    automatic: bool = False,
) -> Dict[str, Any]:
    """Start a people sync for a session the caller may read, or say why not.

    `roster_status` is `db.fetch_roster_status` read with the caller's scope.
    Returns `{status, syncId, started, skipped}`; nothing is enqueued unless
    `started`. In order:

    - Only a member of the session's org starts a sync: the session's stamped
      org must equal the caller's (an admin reading another org's session
      starts nothing, `skipped: other_org`), unless the job would use the
      local operator's `LS_API_KEY` (local dev, no connection id).
    - An active sync is checked against the queue (`_reconcile_sync`) and
      joined, not duplicated.
    - Cooldown: a sync that completed less than `PEOPLE_SYNC_COOLDOWN_MINUTES`
      ago starts nothing (`skipped: cooldown`).
    - Error backoff, for `automatic` starts only: a sync that failed less than
      an hour ago starts nothing (`skipped: error_backoff`).
    - Daily cap: unless this is the session's first initial sync, an org at or
      over `daily_calls_cap()` Livestorm calls in 24 hours starts nothing
      (`skipped: daily_cap`, with `limit` and `used`).

    Then the row (`db.create_livestorm_sync`, which holds `job_connection_id`
    until the claim; a lost race attaches to the winner's row) and the job
    `run_people_sync(sync_id, session_id)`, ids only. A failed enqueue marks
    the row `error`, which clears the connection id at once, and raises
    `PeopleSyncUnavailable`.
    """
    session_id = str(session_id or "").strip()
    caller_org = str(caller_org_id or "").strip()
    session_org = str(session_org_id or "").strip()
    connection_id = str(job_connection_id or "").strip()
    sync = roster_status.get("sync")

    same_org = bool(session_org) and session_org == caller_org
    operator_key = bool(local_dev) and not connection_id
    if not (same_org or operator_key):
        return _start_result(sync, started=False, skipped=SKIPPED_OTHER_ORG)

    sync = _reconcile_sync(sync)
    if sync and sync.get("status") in ACTIVE_SYNC_STATUSES:
        return _start_result(sync, started=False)

    now = _now()
    age = _age(sync.get("updated_at"), now) if sync else None
    if sync and sync.get("status") == "completed" and age is not None and age < timedelta(minutes=PEOPLE_SYNC_COOLDOWN_MINUTES):
        return _start_result(sync, started=False, skipped=SKIPPED_COOLDOWN)
    if automatic and sync and sync.get("status") == "error" and age is not None and age < PEOPLE_SYNC_ERROR_BACKOFF:
        return _start_result(sync, started=False, skipped=SKIPPED_ERROR_BACKOFF)

    first_initial = reason == "initial" and sync is None
    if not first_initial:
        limit = daily_calls_cap()
        used = livestorm_sync_calls_since(session_org, now - DAILY_CALLS_WINDOW) if session_org else 0
        if used >= limit:
            logger.info("People sync for session_id=%s not started: the org's daily call cap is reached", session_id)
            return _start_result(sync, started=False, skipped=SKIPPED_DAILY_CAP, limit=limit, used=used)

    created = create_livestorm_sync(session_id, reason=reason, connection_id=connection_id)
    sync_id = created["sync_id"]
    if not created["created"]:
        return {"status": created["status"], "syncId": sync_id, "started": False, "skipped": None}
    enqueued = enqueue_unique_job_sync("run_people_sync", livestorm_sync_job_id(sync_id), sync_id, session_id)
    if not enqueued:
        # Redis or arq unreachable: close the row now, which clears the
        # connection id, rather than leave it for the sweeper.
        try:
            update_livestorm_sync(sync_id, status="error", error="The job queue was unavailable, so the people sync didn't start. Please try again.")
        except Exception:
            logger.exception("Could not close people sync %s after a failed enqueue", sync_id)
        raise PeopleSyncUnavailable("The job queue is unavailable. Please try again in a moment.")
    return {"status": "pending", "syncId": sync_id, "started": True, "skipped": None}


def request_people_sync(
    session_id: str,
    *,
    read_scope: OrgScope,
    caller_org_id: str,
    job_connection_id: str,
    local_dev: bool = False,
) -> Dict[str, Any]:
    """`POST /api/sessions/{id}/roster-sync`: an explicit sync (a retry after
    an error, or a refresh). The caller's scope is checked first
    (`fetch_session_fields`, which also gives the session's stamped org):
    out of scope raises `RosterSessionNotFound`. The reason is `initial` while
    the session has no roster, `manual` after. Raises `PeopleSyncCapReached`
    when the org's daily cap stops it. Returns `start_people_sync`'s answer
    with the session id."""
    session_id = str(session_id or "").strip()
    fields = fetch_session_fields(session_id, ["organization_id"], organization_id=read_scope)
    if fields is None:
        raise RosterSessionNotFound()
    roster_status = fetch_roster_status(session_id, read_scope)
    if roster_status is None:
        raise RosterSessionNotFound()
    result = start_people_sync(
        session_id,
        reason="initial" if roster_status.get("roster") is None else "manual",
        caller_org_id=caller_org_id,
        session_org_id=str(fields.get("organization_id") or ""),
        job_connection_id=job_connection_id,
        roster_status=roster_status,
        local_dev=local_dev,
    )
    if result.get("skipped") == SKIPPED_DAILY_CAP:
        raise PeopleSyncCapReached(limit=int(result["limit"]), used=int(result["used"]))
    return {"sessionId": session_id, **result}


def start_sync_on_fetch(
    session_id: str,
    *,
    fresh: bool,
    read_scope: OrgScope,
    caller_org_id: str,
    session_org_id: str,
    job_connection_id: str,
    local_dev: bool = False,
) -> Dict[str, Any]:
    """The automatic start: a people sync after a session's base fetch.

    Called by `api_logic.fetch_session_base_data` once the caller's own
    Livestorm credentials returned the session, or the cache answered within
    their scope. `fresh` says the base payloads were just read from
    Livestorm. A sync is started:

    - when the session has no stored roster yet (reason `initial`);
    - after a fresh fetch of a session that has one (reason `manual`): the
      user refreshed the session, so its people are read again too.

    A cache hit on a session that already has a roster starts nothing and
    reports its latest sync. Every start goes through `start_people_sync`
    as an `automatic` one, so all its guards apply, the error backoff
    included: only `POST /roster-sync` retries a failed sync within the hour.

    Returns `{status, started, skipped}` (the response's `rosterSync`), never
    a sync id, a connection id or a person. Raises what the reads and the
    start raise (`RosterSessionNotFound`, `PeopleSyncUnavailable`, database
    errors): the caller decides that a failed start never fails the fetch.
    """
    session_id = str(session_id or "").strip()
    roster_status = fetch_roster_status(session_id, read_scope)
    if roster_status is None:
        raise RosterSessionNotFound()
    has_roster = roster_status.get("roster") is not None
    if has_roster and not fresh:
        sync = roster_status.get("sync")
        return {"status": sync.get("status") if sync else "none", "started": False, "skipped": None}
    result = start_people_sync(
        session_id,
        reason="manual" if has_roster else "initial",
        caller_org_id=caller_org_id,
        session_org_id=session_org_id,
        job_connection_id=job_connection_id,
        roster_status=roster_status,
        local_dev=local_dev,
        automatic=True,
    )
    return {"status": result.get("status"), "started": bool(result.get("started")), "skipped": result.get("skipped")}


# ── Worker side ─────────────────────────────────────────────────────────────


def session_organization(session_id: str) -> str:
    """The session's stamped org ("" when it has none), read in the worker.
    Raises when the session is no longer cached."""
    fields = fetch_session_fields(session_id, ["organization_id"], organization_id=ALL_ORGS)
    if fields is None:
        raise RuntimeError("The session is no longer cached; its people were not read.")
    return str(fields.get("organization_id") or "").strip()


def check_daily_cap(sync_id: str, organization_id: str) -> None:
    """The daily cap again, in the worker, before any Livestorm call.

    The web side checks it before it queues a sync, but it sums only the
    syncs that have ended, so syncs queued together all pass. Called under
    the org's lock, where one sync of the org runs at a time, so every
    earlier sync has recorded its calls: a queued sync that would start at or
    over the cap raises `PeopleSyncCapReached` instead. A sync that starts
    under the cap runs to its end. The session's first `initial` sync is
    exempt, as on the web side (`db.livestorm_sync_is_first_initial`), and so
    is a session without an org (local dev only)."""
    organization_id = str(organization_id or "").strip()
    if not organization_id or livestorm_sync_is_first_initial(sync_id):
        return
    limit = daily_calls_cap()
    used = livestorm_sync_calls_since(organization_id, _now() - DAILY_CALLS_WINDOW)
    if used >= limit:
        raise PeopleSyncCapReached(limit=limit, used=used)


def _resolve_sync_credentials(claim: Mapping[str, Any], *, session_org_id: str) -> str:
    """The Livestorm authorization for a claimed sync, or an error before any call.

    With a connection id, the connection is read from the database (no call)
    and its org must equal the session's stamped org; only then is the token
    resolved, and refreshed if close to expiry (`auth.livestorm_auth_for_job`).
    Without one, the operator's `LS_API_KEY` is used, in local dev only. A
    sync without credentials (an unattended refresh, PR-20) isn't supported
    yet. The value is never logged or returned to a page.
    """
    connection_id = str(claim.get("connection_id") or "").strip()
    session_org = str(session_org_id or "").strip()
    if not connection_id:
        if str(claim.get("credential_user_id") or "").strip():
            raise RuntimeError("Unattended people syncs are not supported yet.")
        if not local_dev_enabled():
            raise RuntimeError("This people sync has no Livestorm credentials. Please reconnect and try again.")
        return livestorm_auth_for_job("")
    connection = fetch_oauth_connection(connection_id)
    if not isinstance(connection, dict):
        raise RuntimeError("The Livestorm connection that started this people sync no longer exists. Please reconnect and try again.")
    connection_org = str(connection.get("organization_id") or "").strip()
    if not session_org or connection_org != session_org:
        raise RuntimeError("This session belongs to another Livestorm organization, so its people were not read.")
    return livestorm_auth_for_job(connection_id)


class _CallCounter:
    """Counts the Livestorm calls of one sync (every HTTP request, retries
    included: `client.requests_sent`), reports each page as progress, and
    stops the sync when its job was cancelled."""

    def __init__(self, session_id: str, should_stop: Optional[Callable[[], bool]]) -> None:
        self.session_id = session_id
        self.should_stop = should_stop
        self.calls = 0

    def check(self) -> None:
        if self.should_stop is not None and self.should_stop():
            raise PeopleSyncStopped("The people sync was cancelled.")

    def page(self, stage: str, label: str) -> Callable[[int], None]:
        def on_page(requests_sent: int) -> None:
            self.calls += max(1, int(requests_sent))
            self.check()
            publish_progress_sync(PROGRESS_KIND, self.session_id, stage, label=label, extra={"calls": self.calls})

        return on_page


def _people_attributes(
    api_key: str,
    organization_id: str,
    *,
    deadline: float,
    on_page: Callable[[int], None],
) -> Optional[List[Mapping[str, Any]]]:
    """The workspace's field labels: the org's stored ones when read less than
    24 hours ago, otherwise read again (and stored). None when Livestorm
    refuses them (`forbidden`): the roster then labels fields from the event.
    Any other failure raises."""
    if organization_id:
        stored = get_org_people_attributes(organization_id)
        age = _age((stored or {}).get("fetched_at"), _now())
        if stored and stored.get("status") in ("ok", "forbidden") and age is not None and age < PEOPLE_ATTRIBUTES_MAX_AGE:
            return list(stored.get("attributes") or []) if stored["status"] == "ok" else None
    listed = livestorm_client.fetch_people_attributes(api_key, deadline=deadline, on_page=on_page)
    if organization_id:
        upsert_org_people_attributes(organization_id, listed["attributes"], status=listed["status"])
    return list(listed["attributes"]) if listed["status"] == "ok" else None


def _first_refresh_due_at(session_end_unix: Optional[float], now: datetime) -> Optional[datetime]:
    """When a first roster is due its refresh: the session's end plus
    `PEOPLE_REFRESH_DELAY_DAYS`. None when the end is unknown, or already more
    than that long ago: this sync then already is the refresh."""
    if session_end_unix is None:
        return None
    due = datetime.fromtimestamp(session_end_unix, tz=timezone.utc) + timedelta(days=PEOPLE_REFRESH_DELAY_DAYS)
    return due if due > now else None


def run_people_sync(
    api_key: str,
    session_id: str,
    sync: Mapping[str, Any],
    *,
    deadline: float,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Read a session's people from Livestorm and save them as its roster. Worker only.

    `sync` is `{sync_id, reason, organization_id}` (the claimed row and the
    session's stamped org). In order: the session's cached payloads (for its
    end and its event's fields); every page of people (`fetch_session_people`,
    which raises rather than return part of a list); the workspace's field
    labels (`_people_attributes`); the field definitions; then
    `db.replace_session_roster` with `source='paginated'`, complete only when
    the people match Livestorm's count. `refresh_due_at` is the session's end
    plus 7 days on a first roster (`_first_refresh_due_at`) and the stored
    value on later ones.

    Any failure (a page, the deadline, `LivestormPaginationError`, a
    database error, `should_stop`) raises before the write, so the previous
    roster stays, and marks the row `error` (its connection id cleared)
    first. On success the row is `completed` with its counts. Returns counts
    only: `{status, calls, people, recordCount}`.
    """
    # Imported here: `record/` is v1 code, and only the worker runs this
    # (tests/test_roster.py lists the modules allowed to import it).
    from livestorm_app.record.clock import clock_base
    from livestorm_app.record.roster import assign_person_refs, event_fields, resolve_field_definitions

    sync_id = str(sync["sync_id"])
    reason = str(sync["reason"])
    organization_id = str(sync.get("organization_id") or "").strip()
    counter = _CallCounter(session_id, should_stop)
    try:
        counter.check()
        cached = fetch_session_fields(session_id, ["session_payload", "event_payload"], organization_id=ALL_ORGS)
        if cached is None:
            raise RuntimeError("The session is no longer cached; its people were not read.")

        publish_progress_sync(PROGRESS_KIND, session_id, "people", label="Reading the session's people…")
        listed = livestorm_client.fetch_session_people(
            api_key, session_id, deadline=deadline, on_page=counter.page("people", "Reading the session's people…"),
        )
        publish_progress_sync(PROGRESS_KIND, session_id, "labels", label="Reading the registration fields…")
        attributes = _people_attributes(
            api_key, organization_id, deadline=deadline, on_page=counter.page("labels", "Reading the registration fields…"),
        )
        definitions, fields_source = resolve_field_definitions(event_fields(cached.get("event_payload")), attributes)

        rows: Dict[str, Dict[str, Any]] = {}
        for record in listed["people"]:
            row = parse_person(record)
            if row:
                rows[row["person_id"]] = row
        refs = assign_person_refs(session_id, list(rows))
        people = [{**row, "person_ref": refs[person_id]} for person_id, row in rows.items()]
        record_count = listed["record_count"]
        complete = None if record_count is None else len(people) == record_count

        stored = fetch_roster_status(session_id, ALL_ORGS)
        if stored and stored.get("roster"):
            refresh_due_at = stored["roster"].get("refresh_due_at")
        else:
            base = clock_base(cached.get("session_payload"))
            end_unix = base.t0_unix + base.end_s if base.t0_unix is not None and base.end_s is not None else None
            refresh_due_at = _first_refresh_due_at(end_unix, _now())

        counter.check()
        publish_progress_sync(PROGRESS_KIND, session_id, "saving", label="Saving the people…")
        saved = replace_session_roster(
            session_id,
            people,
            source="paginated",
            complete=complete,
            record_count=record_count,
            reason=reason,
            fields=definitions,
            fields_source=fields_source,
            refresh_due_at=refresh_due_at,
        )
    except Exception as exc:
        mark_sync_failed(sync_id, exc, calls=counter.calls)
        raise
    update_livestorm_sync(
        sync_id,
        status="completed",
        pages_fetched=counter.calls,
        record_count=record_count,
        people_written=saved["people_count"],
    )
    return {"status": "completed", "calls": counter.calls, "people": saved["people_count"], "recordCount": record_count}
