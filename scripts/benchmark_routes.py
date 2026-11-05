"""Time the heaviest API routes against the seeded LOCAL benchmark data.

Run scripts/seed_benchmark_sessions.py first. Then:

    DATABASE_URL=postgresql://stormiq:stormiq@localhost:5433/stormiq \\
    REDIS_URL=redis://localhost:6379/13 \\
        python scripts/benchmark_routes.py [--runs 7] [--label before]

Requests go through the real app in-process (FastAPI TestClient, signed in
as the seeded `bench-conn` connection), so the numbers cover routing, auth,
database reads, serialisation and Redis, but not the network. For each
route it prints the first (cold) and median latency, the response size (raw and
gzip as a stand-in for the edge's brotli) and how many Postgres
connections one request opened.

Local Postgres has no TLS and sub-millisecond latency, so the connection
count matters more than the local milliseconds: on Render each new
connection is a TLS handshake to the database.

Refuses non-local DATABASE_URL / REDIS_URL, and Redis database 0. The Redis
database is flushed at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import os
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _check_environment() -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    redis_url = os.environ.get("REDIS_URL", "")
    if urlparse(db_url).hostname not in LOCAL_HOSTS:
        sys.exit("Set DATABASE_URL to the local benchmark database.")
    parsed_redis = urlparse(redis_url)
    if parsed_redis.hostname not in LOCAL_HOSTS or parsed_redis.path.strip("/") in ("", "0"):
        sys.exit("Set REDIS_URL to a local Redis with a dedicated database index, e.g. redis://localhost:6379/13.")


_check_environment()

import psycopg  # noqa: E402
import redis  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
from livestorm_app import db  # noqa: E402
from livestorm_app.oauth_client import LIVESTORM_OAUTH_COOKIE  # noqa: E402
from livestorm_app.queue import get_redis_settings, transcription_job_id  # noqa: E402

ROUTES = [
    ("workspace, 10-min session", "/api/sessions/bench-short"),
    ("workspace, 2-h session", "/api/sessions/bench-long"),
    ("v1 record, 2-h session", "/api/sessions/bench-long/record"),
    ("transcript poll (job running)", "/api/sessions/bench-pending/transcript-job"),
    ("AI job poll (job running)", "/api/sessions/bench-pending/deep-analysis/job?language=English"),
    ("card list (42 sessions)", "/api/workspace-sessions"),
    ("bootstrap", "/api/bootstrap"),
]

_connections = {"opened": 0}


def _count_connections() -> None:
    """Count every new Postgres connection, pooled or not."""
    original_class_connect = psycopg.Connection.connect.__func__

    def counting_class_connect(cls, *args, **kwargs):
        _connections["opened"] += 1
        return original_class_connect(cls, *args, **kwargs)

    psycopg.Connection.connect = classmethod(counting_class_connect)
    if hasattr(db, "connect"):
        # db.py bound `connect` at import time, before the patch above.
        def counting_connect(*args, **kwargs):
            _connections["opened"] += 1
            return original_class_connect(psycopg.Connection, *args, **kwargs)

        db.connect = counting_connect


def _queue_pending_transcription() -> None:
    """Make bench-pending's job look alive to arq, so polls don't close it."""
    from arq import create_pool

    job = db.get_transcript_job_for_session("bench-pending")
    if not job or job["status"] not in ("pending", "running"):
        sys.exit("bench-pending has no active transcript job: re-run the seed script with --reset.")

    async def enqueue():
        pool = await create_pool(get_redis_settings())
        await pool.enqueue_job("run_transcription", _job_id=transcription_job_id(job["job_id"]))
        await pool.aclose()

    asyncio.run(enqueue())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    _count_connections()
    _queue_pending_transcription()
    client = TestClient(app_module.app, base_url="http://localhost")
    client.cookies.set(LIVESTORM_OAUTH_COOKIE, "bench-conn")

    rows = []
    try:
        for label, path in ROUTES:
            started = time.perf_counter()
            response = client.get(path)  # first request: cold caches
            first_ms = (time.perf_counter() - started) * 1000
            if response.status_code != 200:
                sys.exit(f"{path} returned {response.status_code}: {response.text[:200]}")
            timings, connections = [], []
            for _ in range(args.runs):
                _connections["opened"] = 0
                started = time.perf_counter()
                response = client.get(path)
                timings.append((time.perf_counter() - started) * 1000)
                connections.append(_connections["opened"])
            body = response.content
            rows.append((label, first_ms, statistics.median(timings), len(body), len(gzip.compress(body, 6)), statistics.median(connections)))
    finally:
        redis.Redis.from_url(os.environ["REDIS_URL"]).flushdb()

    title = f" ({args.label})" if args.label else ""
    print(f"\nBenchmark{title}: {args.runs} runs per route, in-process, local Postgres and Redis\n")
    print("| Route | First request ms | Median ms | Response | Gzipped | DB connections opened |")
    print("|---|---:|---:|---:|---:|---:|")
    for label, first_ms, median_ms, raw, gz, conns in rows:
        print(f"| {label} | {first_ms:.0f} | {median_ms:.0f} | {raw / 1024:,.0f} KB | {gz / 1024:,.0f} KB | {conns:g} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
