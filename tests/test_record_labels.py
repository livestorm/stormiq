"""The chat labels in the v1 record: `record.chatLabels`, `m.questions_total`
and `m.chat_themes` (`record/inputs.chat_labels_view`, `record/measures.py`),
and the record route's cache key for them.

Synthetic session only; the stored labels are built here, as
`db.fetch_chat_labels` returns them.
"""

from datetime import datetime, timezone
from unittest import mock

import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.record.chat_labels import LABEL_VERSION, chat_hash
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.measures import compute_measures

LABELLED_AT = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

# The synthetic chat (synthetic.CHAT), labelled by hand.
CLASSES = {
    "Hello everyone": ("social", None, "en"),
    "Excited for this one": ("social", None, "en"),
    "Welcome! We start in a minute.": ("logistics", None, "en"),
    "Can you hear us?": ("tech", None, "en"),
    "Slides: https://example.test/slides": ("logistics", None, "en"),
    "Hi from Lyon": ("social", None, "en"),
    "Hello from Berlin": ("social", None, "en"),
    "Thanks!": ("social", None, "en"),
    "Great intro": ("content", None, "en"),
    "What about pricing?": ("question", "content", "en"),
    "Happy to cover pricing later": ("content", None, "en"),
    "This dashboard looks useful": ("content", None, "en"),
    "+1": ("content", None, None),
    "Is there a replay?": ("question", "logistics", "en"),
    "Yes, the replay link comes by email": ("logistics", None, "en"),
    "Love the export feature": ("content", None, "en"),
    "Thank you all": ("social", None, "en"),
    "Bye!": ("social", None, "en"),
    "See you next time": ("social", None, "fr"),
}
TEAM_MESSAGES = sum(1 for _offset, _author, flag, _text in synthetic.CHAT if flag)
AUDIENCE_MESSAGES = len(synthetic.CHAT) - TEAM_MESSAGES


def _chat_rows(row):
    events = load_record_inputs(row).events
    chat = events[events["kind"] == "chat"]
    return chat[["eid", "livestorm_id", "text", "is_team"]].to_dict("records")


def stored_labels(**changes):
    rows = _chat_rows(synthetic.cached_row())
    labels = [
        {"id": r["eid"], "livestorm_id": r["livestorm_id"] or None, "class": CLASSES[r["text"]][0],
         "question": CLASSES[r["text"]][1], "language": CLASSES[r["text"]][2]}
        for r in rows
    ]
    hashed = chat_hash([{**r, "livestorm_id": r["livestorm_id"] or None} for r in rows])
    return {
        "label_version": LABEL_VERSION, "chat_hash": hashed, "model": "claude-haiku-5-5",
        "message_count": len(labels), "labels": labels, "created_at": LABELLED_AT, **changes,
    }


def _measures(stored=None):
    return compute_measures(load_record_inputs(synthetic.cached_row(), stored_labels=stored))


def test_without_labels_the_record_says_none_and_the_label_measures_are_unavailable():
    inputs = load_record_inputs(synthetic.cached_row())
    assert inputs.chat_labels is None
    assert inputs.chat_labels_info == {"state": "none", "model": None, "labelledAt": None, "messages": None}
    found = compute_measures(inputs)
    assert found["m.questions_total"].state == found["m.chat_themes"].state == "unavailable"
    assert found["m.questions_total"].coverage == {"missing": ["labels"]}


def test_labels_of_this_chat_are_ready():
    inputs = load_record_inputs(synthetic.cached_row(), stored_labels=stored_labels())
    assert inputs.chat_labels_info == {
        "state": "ready", "model": "claude-haiku-5-5", "labelledAt": LABELLED_AT.isoformat(), "messages": len(synthetic.CHAT),
    }
    assert len(inputs.chat_labels) == len(synthetic.CHAT)


def test_labels_of_another_chat_or_version_are_stale_and_never_read():
    for stale in (stored_labels(chat_hash="0" * 64), stored_labels(label_version=LABEL_VERSION + 1)):
        inputs = load_record_inputs(synthetic.cached_row(), stored_labels=stale)
        assert inputs.chat_labels is None and inputs.chat_labels_info["state"] == "stale"
        assert compute_measures(inputs)["m.chat_themes"].state == "unavailable"


def test_the_question_total_adds_chat_content_questions_and_counts_logistics_apart():
    found = _measures(stored_labels())["m.questions_total"]
    qa = len(synthetic.questions_payload()["data"])
    assert found.state == "ok"
    assert found.value == qa + 1, "one chat question about the content"
    assert found.rows == [
        {"key": "qa", "questions": qa}, {"key": "chat", "questions": 1}, {"key": "chatLogistics", "questions": 1},
    ]
    assert found.coverage == {"chatMessages": AUDIENCE_MESSAGES, "teamMessages": TEAM_MESSAGES, "labels": "model"}


def test_chat_themes_count_the_audiences_messages_by_class():
    found = _measures(stored_labels())["m.chat_themes"]
    assert found.den == AUDIENCE_MESSAGES
    by_key = {row["key"]: row for row in found.rows}
    assert [row["key"] for row in found.rows] == ["content", "question", "tech", "logistics", "social", "laughter", "off_topic"]
    assert sum(row["messages"] for row in found.rows) == AUDIENCE_MESSAGES
    assert by_key["social"]["languages"][0]["language"] == "en"
    assert {"language": "fr", "messages": 1} in by_key["social"]["languages"]
    assert {"language": None, "messages": 1} in by_key["content"]["languages"], "a message with no words has no language"
    assert len(by_key["question"]["ids"]) == 2 and all(i.startswith("C") for i in by_key["question"]["ids"])
    assert found.state == "insufficient", "fewer than 30 messages: counts only"
    assert all(row["share"] is None for row in found.rows) and by_key["social"]["messages"] > 0


def test_the_record_route_reads_the_stamp_on_every_request_and_the_labels_once_per_stamp():
    reads = {"stamps": 0, "labels": 0}
    stored = {"value": None}

    def stamp(session_id, organization_id):
        reads["stamps"] += 1
        value = stored["value"]
        return {key: value[key] for key in ("label_version", "chat_hash", "created_at")} if value else None

    def labels(session_id, organization_id):
        reads["labels"] += 1
        return stored["value"]

    row = synthetic.cached_row()
    with mock.patch.object(api_logic, "fetch_chat_labels_stamp", stamp), mock.patch.object(api_logic, "fetch_chat_labels", labels):
        with synthetic.serving(row, fresh_record_cache=True) as client:
            path = f"/api/sessions/{row['session_id']}/record"
            assert client.get(path).json()["record"]["chatLabels"]["state"] == "none"
            stored["value"] = stored_labels()
            first = client.get(path).json()["record"]
            assert first["chatLabels"]["state"] == "ready" and first["measures"]["m.questions_total"]["state"] == "ok"
            assert client.get(path).json()["record"] == first
            assert (reads["stamps"], reads["labels"]) == (3, 1), "a hit reads the stamp only"
            stored["value"] = stored_labels(created_at=datetime(2026, 11, 11, tzinfo=timezone.utc))
            client.get(path)
            assert reads["labels"] == 2, "a new analysis run misses the cache"


def test_the_cache_key_carries_the_labels_stamp():
    stamp = {"label_version": 1, "chat_hash": "h", "created_at": LABELLED_AT}
    assert api_logic._record_input_versions(None, stamp) == (None, (1, "h", str(LABELLED_AT)), None)


# ── The chat's text by id (`GET /record/chat`) ──────────────────────────────


def _serve_labelled(row):
    labels = stored_labels()
    stamp = {key: labels[key] for key in ("label_version", "chat_hash", "created_at")}
    return (
        mock.patch.object(api_logic, "fetch_chat_labels_stamp", lambda session_id, organization_id: stamp),
        mock.patch.object(api_logic, "fetch_chat_labels", lambda session_id, organization_id: labels),
    )


def test_the_chat_route_gives_every_message_by_the_records_ids():
    row = synthetic.cached_row()
    events = load_record_inputs(row).events
    chat = events[events["kind"] == "chat"]
    with synthetic.serving(row) as client:
        response = client.get(f"/api/sessions/{row['session_id']}/record/chat")
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == ["sessionId", "updatedAt", "messages"]
    messages = body["messages"]
    assert [m["id"] for m in messages] == list(chat["eid"])
    assert [m["text"] for m in messages] == list(chat["text"])
    assert sum(m["team"] for m in messages) == TEAM_MESSAGES
    assert [m["t"] for m in messages] == [int(t) if float(t).is_integer() else t for t in chat["t"]]
    assert all(set(m) == {"id", "t", "text", "team"} for m in messages), "no author, name or ref"


def test_every_chat_theme_row_opens_its_messages():
    row = synthetic.cached_row()
    stamp_patch, labels_patch = _serve_labelled(row)
    with stamp_patch, labels_patch, synthetic.serving(row, fresh_record_cache=True) as client:
        themes = client.get(f"/api/sessions/{row['session_id']}/record").json()["record"]["measures"]["m.chat_themes"]
        texts = {m["id"]: m for m in client.get(f"/api/sessions/{row['session_id']}/record/chat").json()["messages"]}
    for theme in themes["rows"]:
        assert len(theme["ids"]) == theme["messages"]
        for message_id in theme["ids"]:
            assert message_id in texts and not texts[message_id]["team"]
            assert CLASSES[texts[message_id]["text"]][0] == theme["key"]


def test_the_chat_route_is_scoped_and_reads_no_label_or_roster():
    row = synthetic.cached_row()
    untouched = mock.Mock(side_effect=AssertionError("not read"))
    with mock.patch.object(api_logic, "fetch_chat_labels", untouched), mock.patch.object(
        api_logic, "fetch_session_roster", untouched
    ), synthetic.serving(row) as client:
        assert client.get(f"/api/sessions/{row['session_id']}/record/chat").status_code == 200
        missing = client.get("/api/sessions/no-such-session/record/chat")
        assert missing.status_code == 404 and missing.json()["detail"]["resource"] == "Session chat"
        client.cookies.clear()
        assert client.get(f"/api/sessions/{row['session_id']}/record/chat").status_code == 401


def test_a_session_without_a_chat_or_a_clock_answers_with_what_it_has():
    from livestorm_app.record.messages import chat_messages

    assert chat_messages(None, None) == {"messages": []}
    row = synthetic.cached_row()
    without_clock = chat_messages({}, row["chat_payload"])["messages"]
    assert len(without_clock) == len(synthetic.CHAT) and all(m["t"] is None for m in without_clock)


def test_a_bug_in_the_chat_route_returns_500_without_its_detail(monkeypatch):
    from livestorm_app.record import messages

    def broken(session_payload, chat_payload):
        raise RuntimeError("synthetic failure with internal detail")

    monkeypatch.setattr(messages, "chat_messages", broken)
    row = synthetic.cached_row()
    with synthetic.serving(row) as client:
        response = client.get(f"/api/sessions/{row['session_id']}/record/chat")
    assert response.status_code == 500
    assert "internal detail" not in response.text
