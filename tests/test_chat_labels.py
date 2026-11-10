"""The chat labels' contract (`record/chat_labels.py`) and their storage (`db.replace_chat_labels`).

Synthetic chat only. The database tests skip unless TEST_DATABASE_URL points
at a local database (see conftest); org scoping is in test_db_scope.py.
"""

import pytest

from livestorm_app import db
from livestorm_app.db import ALL_ORGS
from livestorm_app.record import chat_labels
from livestorm_app.record.chat_labels import LABEL_VERSION, chat_hash, check_labels


def _label(label_id="C0001", label_class="content", question=None, language="en", livestorm_id="msg-1"):
    return {"id": label_id, "livestorm_id": livestorm_id, "class": label_class, "question": question, "language": language}


def _rows(*texts):
    return [{"eid": f"C{n:04d}", "livestorm_id": f"msg-{n}", "text": text, "minute": n} for n, text in enumerate(texts, start=1)]


# ── chat_hash ───────────────────────────────────────────────────────────────


def test_the_hash_follows_ids_and_texts_only():
    rows = _rows("Hello from Lyon", "Will the slides be shared?")
    moved = [dict(row, minute=row["minute"] + 7, author_ref="P-0000000a") for row in rows]
    assert chat_hash(rows) == chat_hash(moved), "the clock and the author don't change a label"
    assert chat_hash(rows) == chat_hash(list(reversed(rows))), "payload order doesn't matter"
    assert chat_hash(rows) != chat_hash(_rows("Hello from Lyon", "Will the slides be shared ?"))
    assert chat_hash(rows) != chat_hash(rows[:1])
    assert len(chat_hash([])) == 64


def test_the_hash_orders_ids_by_number():
    rows = [{"eid": "C10000", "livestorm_id": "b", "text": "y"}, {"eid": "C9999", "livestorm_id": "a", "text": "x"}]
    assert chat_hash(rows) == chat_hash(list(reversed(rows)))


# ── check_labels ────────────────────────────────────────────────────────────


def test_a_complete_set_is_returned_in_id_order():
    labels = [
        _label("C10000", livestorm_id="m3"),
        _label("C0002", "question", "logistics", "fr", livestorm_id="m2"),
        _label("C0001", "laughter", language=None, livestorm_id=None),
    ]
    checked = check_labels(labels, expected_ids={"C0001", "C0002", "C10000"})
    assert [label["id"] for label in checked] == ["C0001", "C0002", "C10000"]
    assert checked[1] == {"id": "C0002", "livestorm_id": "m2", "class": "question", "question": "logistics", "language": "fr"}


@pytest.mark.parametrize(
    "label, reason",
    [
        ("not a mapping", "mapping"),
        ({**_label(), "text": "copied back"}, "exactly the keys"),
        ({key: value for key, value in _label().items() if key != "language"}, "exactly the keys"),
        (_label("U0001"), "C evidence id"),
        (_label("C1"), "C evidence id"),
        (_label(label_class="praise"), "unknown class"),
        (_label(label_class="question"), "needs its kind"),
        (_label(label_class="question", question="pricing"), "needs its kind"),
        (_label(question="content"), "only a question"),
        (_label(language="EN"), "ISO 639"),
        (_label(language="english"), "ISO 639"),
        (_label(livestorm_id=""), "livestorm_id"),
        (_label(livestorm_id=12), "livestorm_id"),
    ],
)
def test_a_malformed_label_is_refused(label, reason):
    with pytest.raises(ValueError, match=reason):
        check_labels([label])


def test_a_set_must_cover_the_chat_exactly():
    with pytest.raises(ValueError, match="appears twice"):
        check_labels([_label(), _label()])
    with pytest.raises(ValueError, match="1 missing, 0 unknown"):
        check_labels([_label()], expected_ids={"C0001", "C0002"})
    with pytest.raises(ValueError, match="0 missing, 1 unknown"):
        check_labels([_label(), _label("C0002")], expected_ids={"C0001"})
    assert check_labels([], expected_ids=set()) == []


def test_an_error_never_echoes_a_message_text():
    secret = "my phone is 06 12 34 56 78"
    with pytest.raises(ValueError) as caught:
        check_labels([{**_label(), "text": secret}])
    assert secret not in str(caught.value)


def test_every_class_is_documented():
    assert set(chat_labels.LABEL_CLASSES) == {"content", "question", "tech", "logistics", "social", "laughter", "off_topic"}
    assert chat_labels.QUESTION_KINDS == ("content", "logistics")


# ── Storage ─────────────────────────────────────────────────────────────────

HASH = "a" * 64


def test_labels_are_stored_whole_and_replaced(test_database):
    db.upsert_cached_session("key-a", "sess-a", organization_id="org-a")
    first = [_label("C0002", "question", "content"), _label("C0001", "social")]
    assert db.replace_chat_labels("sess-a", first, chat_hash=HASH, model="claude-haiku-5-5") == {
        "label_version": LABEL_VERSION, "message_count": 2,
    }
    stored = db.fetch_chat_labels("sess-a", "org-a")
    assert stored["labels"] == check_labels(first)
    assert (stored["chat_hash"], stored["model"], stored["message_count"], stored["label_version"]) == (
        HASH, "claude-haiku-5-5", 2, LABEL_VERSION,
    )

    db.replace_chat_labels("sess-a", [_label("C0001", "tech")], chat_hash="b" * 64, model="claude-sonnet-5-5")
    stored = db.fetch_chat_labels("sess-a", "org-a")
    assert [label["class"] for label in stored["labels"]] == ["tech"]
    assert (stored["chat_hash"], stored["message_count"]) == ("b" * 64, 1)


def test_a_bad_set_or_a_missing_session_keeps_the_previous_labels(test_database):
    db.upsert_cached_session("key-a", "sess-a", organization_id="org-a")
    db.replace_chat_labels("sess-a", [_label()], chat_hash=HASH, model="claude-haiku-5-5")
    with pytest.raises(ValueError):
        db.replace_chat_labels("sess-a", [_label(label_class="praise")], chat_hash=HASH, model="claude-haiku-5-5")
    with pytest.raises(ValueError, match="SHA-256"):
        db.replace_chat_labels("sess-a", [_label()], chat_hash="ABC", model="claude-haiku-5-5")
    with pytest.raises(ValueError, match="model"):
        db.replace_chat_labels("sess-a", [_label()], chat_hash=HASH, model=" ")
    with pytest.raises(RuntimeError, match="previous labels are kept"):
        db.replace_chat_labels("sess-missing", [_label()], chat_hash=HASH, model="claude-haiku-5-5")
    assert db.fetch_chat_labels("sess-a", "org-a")["labels"] == [_label()]
    assert db.fetch_chat_labels("sess-missing", ALL_ORGS) is None


def test_deleting_a_session_deletes_its_labels(test_database):
    db.upsert_cached_session("key-a", "sess-a", organization_id="org-a")
    db.replace_chat_labels("sess-a", [_label()], chat_hash=HASH, model="claude-haiku-5-5")
    assert db.admin_delete_session("sess-a") is True
    db.upsert_cached_session("key-a", "sess-a", organization_id="org-a")
    assert db.fetch_chat_labels("sess-a", "org-a") is None
