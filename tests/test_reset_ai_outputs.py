"""The reset of AI outputs (`db.count_ai_outputs`, `db.reset_ai_outputs`,
`scripts/reset_ai_outputs.py`) on the local Postgres: a dry run changes
nothing; a run removes every AI output and keeps what the session was
fetched with, its transcript, the host's own answers, settings and usage."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from livestorm_app import db

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "reset_ai_outputs.py"


def _script():
    spec = importlib.util.spec_from_file_location("reset_ai_outputs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed():
    bundles = {column: {"English": f"{column} text"} for column in db.AI_OUTPUT_COLUMNS}
    db.upsert_cached_session(
        "key-a",
        "sess-r",
        session_payload={"name": "R"},
        chat_payload={"data": []},
        transcript_payload={"result": {}},
        organization_id="org-r",
        **bundles,
    )
    with db.get_db_connection() as connection:
        connection.execute(
            "UPDATE session_cache SET cover_image_bytes = %s, cover_image_mime = 'image/png' WHERE session_id = 'sess-r'",
            (b"png",),
        )
        connection.execute(
            """
            INSERT INTO output_versions (session_id, output_column, entry_key, content)
            VALUES ('sess-r', 'analysis_bundle', 'English', '"old"'::jsonb)
            """
        )
        connection.execute(
            """
            INSERT INTO session_chat_labels (session_id, label_version, chat_hash, model, message_count, labels)
            VALUES ('sess-r', 1, 'h', 'm', 0, '{}'::jsonb)
            """
        )
        connection.execute(
            """
            INSERT INTO session_understanding (session_id, version, dossier_hash, model, result)
            VALUES ('sess-r', 1, 'h', 'm', '{}'::jsonb)
            """
        )
        connection.execute(
            "INSERT INTO session_faq (session_id, entries) VALUES ('sess-r', %s::jsonb)", (json.dumps([]),)
        )
        connection.execute(
            """
            INSERT INTO session_question_replies (session_id, source, livestorm_id, evidence_id, answered, reply, located)
            VALUES ('sess-r', 'qa', 'q1', 'Q001', TRUE, 'Yes, on Friday.', 'confirmed')
            """
        )
        connection.execute(
            "INSERT INTO session_figure_checks (session_id, check_key, choice) VALUES ('sess-r', 'nps|61', 'NPS')"
        )
        connection.execute(
            "INSERT INTO system_settings (key, value) VALUES ('tts_voice', 'cedar') ON CONFLICT (key) DO NOTHING"
        )
        connection.commit()


def _row():
    with db.get_db_connection() as connection:
        return connection.execute("SELECT * FROM session_cache WHERE session_id = 'sess-r'").fetchone()


def _count(table, where="TRUE"):
    with db.get_db_connection() as connection:
        return connection.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}").fetchone()["n"]


def test_a_dry_run_counts_and_changes_nothing(test_database):
    _seed()
    lines = []
    counts = _script().run(apply=False, out=lines.append)
    assert all(counts[f"session_cache.{column}"] == 1 for column in db.AI_OUTPUT_COLUMNS)
    seeded = [table for table in db.AI_OUTPUT_TABLES if table not in ("surprise_storyboards", "surprise_voice_clips")]
    assert all(counts[table] == 1 for table in seeded)
    assert counts["session_question_replies.located"] == 1
    assert "session_cache.cover_image" not in counts, "covers only when asked"
    assert lines[-1].startswith("Dry run: nothing was changed")
    assert _row()["analysis_bundle"] is not None and _count("output_versions") == 1


def test_a_run_removes_every_ai_output_and_keeps_the_rest(test_database):
    _seed()
    _script().run(apply=True, out=lambda line: None)
    row = _row()
    assert all(row[column] is None for column in db.AI_OUTPUT_COLUMNS)
    assert all(_count(table) == 0 for table in db.AI_OUTPUT_TABLES)
    # Kept: what the session was fetched with, its transcript and cover, the host's answers, settings.
    assert (row["session_payload"], row["transcript_payload"], row["organization_id"]) == ({"name": "R"}, {"result": {}}, "org-r")
    assert bytes(row["cover_image_bytes"]) == b"png"
    with db.get_db_connection() as connection:
        reply = connection.execute("SELECT answered, reply, located FROM session_question_replies").fetchone()
    assert (reply["answered"], reply["reply"], reply["located"]) == (True, "Yes, on Friday.", None)
    assert _count("session_figure_checks") == 1 and _count("system_settings", "key = 'tts_voice'") == 1
    assert db.count_ai_outputs()["session_cache.analysis_bundle"] == 0, "a second run has nothing left"


def test_covers_go_only_when_asked(test_database):
    _seed()
    done = db.reset_ai_outputs(include_covers=True)
    assert done["session_cache.cover_image"] == 1
    assert _row()["cover_image_bytes"] is None


def test_a_failed_write_changes_nothing(test_database, monkeypatch):
    _seed()
    monkeypatch.setattr(db, "AI_OUTPUT_TABLES", (*db.AI_OUTPUT_TABLES, "no_such_table"))
    with pytest.raises(RuntimeError, match="nothing was changed"):
        db.reset_ai_outputs()
    assert _row()["analysis_bundle"] is not None and _count("output_versions") == 1


def test_the_script_refuses_a_remote_database_and_needs_an_explicit_url(monkeypatch):
    script = _script()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="Set DATABASE_URL explicitly"):
        script._require_database(False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example.com:5432/x")
    with pytest.raises(SystemExit, match="not a local database"):
        script._require_database(False)
    script._require_database(True)
