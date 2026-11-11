"""The v1 session analysis the user starts (`api_logic.start_session_analysis`,
`run_session_analysis`, `worker.run_session_analysis_job`).

Synthetic session only. The model is a fake `llm_client.generate_json`, the
labels' storage an in-memory dict, so no test makes a call or needs a database.
Access control for the two routes is in test_access_control.py.
"""

import asyncio

import pytest
import synthetic_session as synthetic

from livestorm_app import api_logic, db, llm_client, worker
from livestorm_app.ai.chat_labelling import ChatLabelBatch
from livestorm_app.ai.understanding import SessionUnderstanding
from livestorm_app.record.chat_labels import LABEL_VERSION

JOB_ID = f"session_analysis:{synthetic.SESSION_ID}:_"


# ── Starting and polling ────────────────────────────────────────────────────


@pytest.fixture
def queue(monkeypatch):
    calls = {"enqueued": [], "reads": []}
    monkeypatch.setattr(
        api_logic, "fetch_session_fields",
        lambda session_id, fields, organization_id: {"has_transcript": True}
        if (session_id, organization_id) == (synthetic.SESSION_ID, synthetic.ORGANIZATION_ID) else None,
    )

    def enqueue(function_name, job_id, *args, **kwargs):
        calls["enqueued"].append((function_name, job_id, args, kwargs))
        return {"jobId": job_id, "jobStatus": "pending", "enqueued": True}

    monkeypatch.setattr(api_logic, "enqueue_unique_job_sync", enqueue)
    monkeypatch.setattr(api_logic, "read_job_status_sync", lambda job_id: calls["reads"].append(job_id) or {"jobStatus": "running"})
    return calls


def test_starting_queues_one_run_per_session_with_the_session_id_only(queue):
    started = api_logic.start_session_analysis(synthetic.SESSION_ID, organization_id=synthetic.ORGANIZATION_ID)
    assert started == {"jobId": JOB_ID, "jobStatus": "pending", "enqueued": True}
    assert queue["enqueued"] == [("run_session_analysis_job", JOB_ID, (synthetic.SESSION_ID,), {})]


def test_a_session_outside_the_scope_starts_nothing(queue):
    with pytest.raises(api_logic.SessionNotFoundError):
        api_logic.start_session_analysis(synthetic.SESSION_ID, organization_id="another-org")
    with pytest.raises(api_logic.SessionNotFoundError):
        api_logic.get_session_analysis_job(synthetic.SESSION_ID, JOB_ID, organization_id="another-org")
    assert queue["enqueued"] == [] and queue["reads"] == []


def test_a_queue_that_is_down_fails_the_start(queue, monkeypatch):
    monkeypatch.setattr(api_logic, "enqueue_unique_job_sync", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="queue is unavailable"):
        api_logic.start_session_analysis(synthetic.SESSION_ID, organization_id=synthetic.ORGANIZATION_ID)


def test_the_poll_reads_the_sessions_own_run_whatever_id_is_sent(queue):
    status = api_logic.get_session_analysis_job(
        synthetic.SESSION_ID, "session_analysis:other-orgs-session:_", organization_id=synthetic.ORGANIZATION_ID,
    )
    assert status == {"jobId": JOB_ID, "jobStatus": "running"}
    assert queue["reads"] == [JOB_ID]


# ── The run ─────────────────────────────────────────────────────────────────


@pytest.fixture
def run(monkeypatch):
    state = {"row": synthetic.cached_row(), "stored": None, "understood": None, "calls": 0, "kinds": [], "fail": None}
    monkeypatch.setattr(api_logic, "_require_cached_payloads", lambda session_id, organization_id: state["row"])
    monkeypatch.setattr(llm_client, "get_provider", lambda: "anthropic")

    def fake_model(kind, schema, request, *, api_key, ctx):
        state["calls"] += 1
        state["kinds"].append(kind)
        state["ctx"] = ctx
        if state["fail"]:
            raise state["fail"]
        if kind == "understanding":
            state["dossier"] = request.context
            return SessionUnderstanding.model_validate(
                {
                    "chapters": [{"title": "The whole session", "kind": "content", "first_utterance": "U0001", "last_utterance": "U0002"}],
                    "answers": [], "prompts": [], "figures": [], "groups": [],
                }
            )
        ids = [line.split("\t")[0] for line in request.payload.splitlines()]
        return ChatLabelBatch.model_validate(
            {"labels": [{"id": label_id, "label": "social", "question": None, "language": "en"} for label_id in ids]}
        )

    monkeypatch.setattr(llm_client, "generate_json", fake_model)
    monkeypatch.setattr(db, "fetch_chat_labels", lambda session_id, organization_id: state["stored"])
    monkeypatch.setattr(db, "fetch_understanding", lambda session_id, organization_id: state["understood"])

    def replace(session_id, labels, *, chat_hash, model):
        state["stored"] = {"label_version": LABEL_VERSION, "chat_hash": chat_hash, "model": model, "labels": labels}
        return {"label_version": LABEL_VERSION, "message_count": len(labels)}

    def replace_understanding(session_id, result, *, dossier_hash, model):
        state["understood"] = {"version": result["version"], "dossier_hash": dossier_hash, "model": model, "result": result}

    monkeypatch.setattr(db, "replace_chat_labels", replace)
    monkeypatch.setattr(db, "replace_understanding", replace_understanding)
    return state


def _edited_chat():
    chat = synthetic.chat_payload()
    chat["data"][0]["attributes"]["text_content"] = "Hello everyone, edited"
    return chat


def test_a_run_labels_every_chat_message_and_stores_them(run):
    result = api_logic.run_session_analysis("key-1", synthetic.SESSION_ID)
    assert result == {"chatLabels": "labelled", "messages": len(synthetic.CHAT), "understanding": "read"}
    assert len(run["stored"]["labels"]) == len(synthetic.CHAT) and run["stored"]["model"] == "claude-haiku-5-5"
    assert run["stored"]["labels"][0]["livestorm_id"] == "msg-001"
    assert run["kinds"] == ["chat_labels", "understanding"], "the labels first, then the session read once"
    ctx = run["ctx"]
    assert (ctx.job_kind, ctx.session_id, ctx.organization_id, ctx.job_id) == (
        "understanding", synthetic.SESSION_ID, synthetic.ORGANIZATION_ID, JOB_ID,
    )
    understood = run["understood"]
    assert understood["model"] == "claude-sonnet-5-5" and understood["dossier_hash"] == run["dossier"].sha256
    assert [c["title"] for c in understood["result"]["chapters"]] == ["The whole session"]


def test_an_unchanged_chat_is_not_labelled_again(run):
    api_logic.run_session_analysis("key-1", synthetic.SESSION_ID)
    again = api_logic.run_session_analysis("key-1", synthetic.SESSION_ID)
    assert (again["chatLabels"], again["understanding"]) == ("up_to_date", "up_to_date")
    assert run["kinds"] == ["chat_labels", "understanding"], "nothing changed: no call the second time"


def test_a_changed_chat_or_an_older_label_version_is_labelled_again(run):
    api_logic.run_session_analysis("key-1", synthetic.SESSION_ID)
    run["stored"] = dict(run["stored"], label_version=LABEL_VERSION - 1)
    assert api_logic.run_session_analysis("key-1", synthetic.SESSION_ID)["chatLabels"] == "labelled"
    run["row"] = dict(run["row"], chat_payload=_edited_chat())
    assert api_logic.run_session_analysis("key-1", synthetic.SESSION_ID)["chatLabels"] == "labelled"
    assert run["kinds"].count("chat_labels") == 3


def test_a_failed_run_keeps_the_earlier_labels(run):
    api_logic.run_session_analysis("key-1", synthetic.SESSION_ID)
    before = run["stored"]
    run["row"] = dict(run["row"], chat_payload=_edited_chat())
    run["fail"] = llm_client.LLMRefused("refused")
    with pytest.raises(llm_client.LLMRefused):
        api_logic.run_session_analysis("key-1", synthetic.SESSION_ID)
    assert run["stored"] is before


def test_a_session_without_chat_makes_no_call(run):
    run["row"] = dict(run["row"], chat_payload={"data": []})
    result = api_logic.run_session_analysis("key-1", synthetic.SESSION_ID)
    assert (result["chatLabels"], result["messages"]) == ("labelled", 0)
    assert "chat_labels" not in run["kinds"] and run["stored"]["labels"] == []


def test_a_session_without_a_transcript_reads_nothing_more(run):
    run["row"] = dict(run["row"], transcript_payload=None)
    assert api_logic.run_session_analysis("key-1", synthetic.SESSION_ID)["understanding"] == "no_transcript"
    assert run["kinds"] == ["chat_labels"] and run["understood"] is None


def test_new_questions_or_a_new_transcript_read_the_session_again(run):
    api_logic.run_session_analysis("key-1", synthetic.SESSION_ID)
    first = run["understood"]["dossier_hash"]
    questions = synthetic.questions_payload()
    questions["data"][3]["attributes"]["question"] = "Is there a public API?"
    run["row"] = dict(run["row"], questions_payload=questions)
    assert api_logic.run_session_analysis("key-1", synthetic.SESSION_ID)["understanding"] == "read"
    assert run["understood"]["dossier_hash"] != first
    assert run["kinds"].count("understanding") == 2


def test_a_failed_read_keeps_the_labels_just_made_and_the_earlier_reading(run):
    api_logic.run_session_analysis("key-1", synthetic.SESSION_ID)
    before = run["understood"]
    questions = synthetic.questions_payload()
    questions["data"][3]["attributes"]["question"] = "Is there a public API?"
    run["row"] = dict(run["row"], questions_payload=questions)

    original = llm_client.generate_json

    def refuse_understanding(kind, *args, **kwargs):
        if kind == "understanding":
            raise llm_client.LLMRefused("refused")
        return original(kind, *args, **kwargs)

    llm_client.generate_json = refuse_understanding
    try:
        with pytest.raises(llm_client.LLMRefused):
            api_logic.run_session_analysis("key-1", synthetic.SESSION_ID)
    finally:
        llm_client.generate_json = original
    assert run["understood"] is before


def test_the_worker_reads_the_key_itself_and_passes_the_session_id_on(monkeypatch):
    seen = []
    monkeypatch.setattr(worker, "_read_llm_key_or_raise", lambda: "key-from-env")
    monkeypatch.setattr(api_logic, "run_session_analysis", lambda key, session_id: seen.append((key, session_id)) or {"chatLabels": "labelled"})
    assert asyncio.run(worker.run_session_analysis_job({}, synthetic.SESSION_ID)) == {"chatLabels": "labelled"}
    assert seen == [("key-from-env", synthetic.SESSION_ID)]
