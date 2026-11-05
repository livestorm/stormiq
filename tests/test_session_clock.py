"""The session clock and audience evidence ids (`livestorm_app/record/`).

Pure functions on synthetic payloads: no database, Redis or network.
"""

from __future__ import annotations

import copy
import importlib.util
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from livestorm_app.record import clock, evidence
from livestorm_app.record.clock import AFTER, BEFORE, LIVE, MINUTE_COLUMNS

T0 = int(datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc).timestamp())
HOUR = 3600


def session(**attributes):
    return {"data": {"id": "s1", "type": "sessions", "attributes": attributes}}


def one_hour_session(**overrides):
    attributes = {"started_at": T0, "ended_at": T0 + HOUR, "duration": HOUR, "estimated_started_at": T0}
    attributes.update(overrides)
    return session(**attributes)


def chat(*messages):
    data = []
    for index, message in enumerate(messages):
        message = dict(message)
        message_id = message.pop("id", f"m{index:03d}")
        attributes = {"text_content": "hello", "author_id": f"p{index}", "from_team_member": False, "from_guest_speaker": False}
        attributes.update(message)
        data.append({"id": message_id, "type": "chat_messages", "attributes": attributes})
    return {"data": data}


def questions(*items):
    data = []
    for index, item in enumerate(items):
        item = dict(item)
        question_id = item.pop("id", f"q{index:03d}")
        attributes = {"question": "How?", "question_author_id": f"a{index}", "response": None, "responded_at": None}
        attributes.update(item)
        data.append({"id": question_id, "type": "questions", "attributes": attributes})
    return {"data": data}


def at(seconds):
    """A raw `created_at`: unix seconds, `seconds` after the start."""
    return T0 + seconds


def bins(chat_payload=None, questions_payload=None, session_payload=None, speech=None):
    base = clock.clock_base(session_payload or one_hour_session())
    events = evidence.audience_events(chat_payload, questions_payload, base)
    return events, clock.minute_bins(events, speech, base=base)


@pytest.fixture
def local_time_not_utc(monkeypatch):
    """Run with a local zone that isn't UTC (CI runs in UTC), so a time read
    in local time instead of UTC shows up."""
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


# ── Clock basics ────────────────────────────────────────────────────────────


def test_chat_and_question_times_become_seconds_since_started_at():
    events, _ = bins(
        chat({"created_at": at(90)}, {"created_at": at(125.5)}),
        questions({"created_at": at(600), "response": "Like this.", "responded_at": at(660)}),
    )
    by_eid = events.set_index("eid")
    assert by_eid.loc["C0001", "t"] == 90
    assert by_eid.loc["C0002", "t"] == 125.5
    assert by_eid.loc["Q001", "t"] == 600
    assert by_eid.loc["A001", "t"] == 660
    assert list(events["phase"]) == [LIVE] * 4
    assert list(events["minute"]) == [1, 2, 10, 11]


@pytest.mark.parametrize(
    "raw, expected_t",
    [
        (at(3240), 3240.0),
        (float(at(3240)), 3240.0),
        (at(3240) + 0.25, 3240.25),
        (str(at(3240)), 3240.0),
        ("2026-10-01T09:54:00Z", 3240.0),
        ("2026-10-01T09:54:00.500Z", 3240.5),
        ("2026-10-01T11:54:00+02:00", 3240.0),
        ("2026-10-01T09:54:00", 3240.0),  # no zone: read as UTC
        ("2026-10-01 09:54:00+00:00", 3240.0),
    ],
    ids=["epoch-int", "epoch-float", "epoch-fraction", "epoch-string", "iso-z", "iso-millis", "iso-offset", "iso-naive", "iso-space"],
)
@pytest.mark.usefixtures("local_time_not_utc")
def test_created_at_is_read_from_epoch_seconds_and_iso_strings(raw, expected_t):
    events, series = bins(chat({"created_at": raw}))
    assert events.loc[0, "t"] == pytest.approx(expected_t)
    assert events.loc[0, "minute"] == 54
    assert series.minutes.loc[54, "audience_chat"] == 1


@pytest.mark.usefixtures("local_time_not_utc")
def test_an_iso_time_without_a_zone_is_utc_whatever_the_local_zone():
    assert clock.parse_time("2026-10-01T09:54:00") == clock.parse_time("2026-10-01T09:54:00Z")


@pytest.mark.parametrize("raw", [None, "", "  ", "soon", True, 0, -5, float("nan"), float("inf"), {"at": 1}])
def test_unreadable_times_are_missing_not_zero(raw):
    assert clock.parse_time(raw) is None


def test_session_times_may_be_iso_strings_too():
    base = clock.clock_base(session(started_at="2026-10-01T09:00:00Z", ended_at="2026-10-01T10:00:00Z", duration="3600"))
    assert base.t0_unix == T0
    assert base.end_s == HOUR
    assert base.duration_attr_s == HOUR


def test_messages_at_54_57_and_59_5_minutes_land_in_minutes_54_57_and_59():
    events, series = bins(chat({"created_at": at(54 * 60)}, {"created_at": at(57 * 60)}, {"created_at": at(59.5 * 60)}))
    assert list(events["minute"]) == [54, 57, 59]
    assert len(series.minutes) == 60
    counts = series.minutes.set_index("minute")["audience_chat"]
    assert counts[54] == counts[57] == counts[59] == 1
    assert counts.sum() == 3


def test_waiting_room_chat_goes_before_and_does_not_shift_live_minutes():
    live_only = chat({"id": "live1", "created_at": at(120)}, {"id": "live2", "created_at": at(30 * 60)})
    with_waiting_room = chat(
        {"id": "wait1", "created_at": at(-600)},
        {"id": "wait2", "created_at": at(-30), "from_team_member": True},
        {"id": "live1", "created_at": at(120)},
        {"id": "live2", "created_at": at(30 * 60)},
    )
    _, without = bins(live_only)
    events, series = bins(with_waiting_room)

    pd.testing.assert_frame_equal(series.minutes, without.minutes)
    assert list(events["phase"]) == [BEFORE, BEFORE, LIVE, LIVE]
    assert events["minute"].isna().tolist() == [True, True, False, False]
    before = series.edges[BEFORE]
    assert before["counts"]["audience_chat"] == 1
    assert before["counts"]["team_chat"] == 1
    assert before["first_t"] == -600
    assert series.edges[AFTER]["counts"]["audience_chat"] == 0


def test_the_edges_keep_the_earliest_and_the_latest_time_across_kinds():
    # Rows come chat first, then questions, then answers, so the chat time is
    # seen first on each edge: the earliest and latest must still win.
    _, series = bins(
        chat({"created_at": at(-30)}, {"created_at": at(HOUR + 100)}),
        questions(
            {"created_at": at(-600)},
            {"created_at": at(300), "response": "Late.", "responded_at": at(HOUR + 10)},
        ),
    )
    assert series.edges[BEFORE]["first_t"] == -600
    assert series.edges[BEFORE]["counts"]["audience_chat"] == 1
    assert series.edges[BEFORE]["counts"]["questions"] == 1
    assert series.edges[AFTER]["last_t"] == HOUR + 100
    assert series.edges[AFTER]["counts"]["answers"] == 1


@pytest.mark.parametrize("end_s, last_minute", [(HOUR, 59), (59.5 * 60, 59), (HOUR + 1, 60)])
def test_an_event_exactly_at_the_end_lands_in_the_last_minute(end_s, last_minute):
    session_payload = one_hour_session(ended_at=T0 + end_s)
    events, series = bins(chat({"created_at": at(end_s)}, {"created_at": at(end_s + 1)}), session_payload=session_payload)
    assert events.loc[0, "phase"] == LIVE
    assert events.loc[0, "minute"] == last_minute
    assert series.minutes["minute"].max() == last_minute
    assert series.minutes.loc[last_minute, "audience_chat"] == 1
    assert events.loc[1, "phase"] == AFTER
    assert series.edges[AFTER]["counts"]["audience_chat"] == 1
    assert series.edges[AFTER]["last_t"] == end_s + 1


def test_empty_minutes_are_zero_filled():
    session_payload = one_hour_session(ended_at=T0 + 600)
    _, series = bins(chat({"created_at": at(65)}), questions({"created_at": at(310)}), session_payload=session_payload)
    minutes = series.minutes
    assert list(minutes.columns) == list(MINUTE_COLUMNS)
    assert minutes["minute"].tolist() == list(range(10))
    assert minutes["audience_chat"].tolist() == [0, 1, 0, 0, 0, 0, 0, 0, 0, 0]
    assert minutes["questions"].tolist() == [0, 0, 0, 0, 0, 1, 0, 0, 0, 0]
    assert minutes[["team_chat", "answers", "speaking_s", "words"]].to_numpy().sum() == 0
    assert str(minutes["audience_chat"].dtype) == "int64"


def test_lanes_split_audience_chat_team_chat_questions_and_answers():
    _, series = bins(
        chat(
            {"created_at": at(10)},
            {"created_at": at(20), "from_team_member": True},
            {"created_at": at(30), "from_guest_speaker": "true"},
        ),
        questions(
            {"created_at": at(40), "response": "Yes.", "responded_at": at(70)},
            {"created_at": at(50), "response_orally": True},  # answered orally: no written answer
        ),
    )
    minute_0 = series.minutes.loc[0]
    assert (minute_0["audience_chat"], minute_0["team_chat"], minute_0["questions"]) == (1, 2, 2)
    assert series.minutes.loc[1, "answers"] == 1
    assert series.minutes["answers"].sum() == 1


def test_speech_is_split_across_minute_borders_and_the_start():
    speech = pd.DataFrame({"t_start": [50.0, -5.0, 3590.0], "t_end": [70.0, 5.0, 3610.0], "n_words": [10, 4, 6]})
    _, series = bins(speech=speech)
    minutes = series.minutes.set_index("minute")
    assert minutes.loc[0, "speaking_s"] == 15  # 50-60, plus 0-5
    assert minutes.loc[0, "words"] == 7  # 5 + 2
    assert minutes.loc[1, "speaking_s"] == 10
    assert minutes.loc[1, "words"] == 5
    assert minutes.loc[59, "speaking_s"] == 10
    assert series.edges[BEFORE]["counts"]["speaking_s"] == 5
    assert series.edges[BEFORE]["counts"]["words"] == 2
    assert series.edges[BEFORE]["first_t"] == -5
    assert series.edges[AFTER]["counts"]["speaking_s"] == 10
    assert series.edges[AFTER]["last_t"] == 3610


def test_phase_and_labels():
    base = clock.clock_base(one_hour_session())
    assert clock.phase(-0.5, base) == BEFORE
    assert clock.phase(0, base) == LIVE
    assert clock.phase(HOUR, base) == LIVE
    assert clock.phase(HOUR + 0.5, base) == AFTER
    assert clock.phase(None, base) is None
    assert clock.session_label(750, base) == "12:30"
    assert clock.session_label(750.9, base) == "12:30"
    assert clock.session_label(-130, base) == "−02:10"
    assert clock.session_label(-0.2, base) == "−00:01"
    assert clock.session_label(HOUR + 65, base) == "+01:05"
    assert clock.session_label(HOUR + 0.2, base) == "+00:01"
    assert clock.session_label(None, base) == ""
    long_base = clock.clock_base(one_hour_session(ended_at=T0 + 3 * HOUR))
    assert clock.session_label(7530, long_base) == "125:30"


def test_to_session_s_reads_raw_values():
    base = clock.clock_base(one_hour_session())
    assert clock.to_session_s(at(42), base) == 42
    assert clock.to_session_s("2026-10-01T08:59:00Z", base) == -60
    assert clock.to_session_s(None, base) is None


# ── Clock fallbacks ─────────────────────────────────────────────────────────


def test_end_comes_from_ended_at_minus_started_at():
    base = clock.clock_base(one_hour_session(ended_at=T0 + 3655, duration=3600))
    assert (base.end_s, base.end_source) == (3655, "ended_at")
    assert base.duration_attr_s == 3600  # kept, to report the gap


@pytest.mark.parametrize(
    "ended_at",
    [None, T0 - 10, T0, "not a time"],
    ids=["missing", "before-start", "at-start", "unreadable"],
)
def test_end_comes_from_duration_when_ended_at_is_missing_or_unusable(ended_at):
    base = clock.clock_base(one_hour_session(ended_at=ended_at, duration=2700))
    assert (base.end_s, base.end_source) == (2700, "duration")
    assert clock.minute_count(base) == 45


def test_without_an_end_minutes_run_to_the_last_live_event():
    session_payload = session(started_at=T0)
    base = clock.clock_base(session_payload)
    assert (base.end_s, base.end_source) == (None, "none")
    events, series = bins(chat({"created_at": at(400)}, {"created_at": at(5 * HOUR)}), session_payload=session_payload)
    assert list(events["phase"]) == [LIVE, LIVE]
    assert len(series.minutes) == 301  # minutes 0 to 300
    assert series.minutes.loc[300, "audience_chat"] == 1
    assert series.edges[AFTER]["counts"]["audience_chat"] == 0


def test_no_started_at_means_no_clock_and_never_estimated_started_at():
    session_payload = session(estimated_started_at=T0, ended_at=T0 + HOUR, duration=HOUR)
    base = clock.clock_base(session_payload)
    assert base.t0_unix is None
    assert not base.has_clock
    assert base.planned_t0_unix == T0
    assert base.late_start_s is None
    assert clock.to_session_s(at(60), base) is None

    events, series = bins(
        chat({"created_at": at(60)}, {"created_at": at(120)}),
        questions({"created_at": at(300), "response": "Yes.", "responded_at": at(360)}),
        session_payload=session_payload,
    )
    assert events["t"].isna().all()
    assert events["phase"].isna().all()
    assert events["minute"].isna().all()
    assert events["at_unix"].tolist() == [at(60), at(120), at(300), at(360)]
    assert series.minutes.empty
    assert list(series.minutes.columns) == list(MINUTE_COLUMNS)
    assert series.unplaced == 4
    assert series.edges[BEFORE]["counts"]["audience_chat"] == 0


def test_nothing_is_placed_on_a_clock_that_does_not_exist():
    base = clock.clock_base(session(ended_at=T0 + HOUR, duration=HOUR))
    assert base.end_s == HOUR  # the length is known, the start is not
    for t in (-30, 0, 100, HOUR, HOUR + 5):
        assert clock.phase(t, base) is None
        assert clock.minute_index(t, base) is None
        assert clock.session_label(t, base) == ""


@pytest.mark.parametrize("started_at", [None, 0, "", "later"], ids=["none", "zero", "empty", "unreadable"])
def test_a_missing_or_unreadable_start_gives_no_clock(started_at):
    assert not clock.clock_base(one_hour_session(started_at=started_at)).has_clock


def test_late_start_compares_started_at_with_the_planned_start():
    base = clock.clock_base(one_hour_session(estimated_started_at=T0 - 150))
    assert base.late_start_s == 150
    assert base.t0_unix == T0


@pytest.mark.parametrize("payload", [None, {}, [], {"data": []}, {"data": {"attributes": None}}, "x"])
def test_unusable_session_payloads_give_no_clock(payload):
    base = clock.clock_base(payload)
    assert not base.has_clock
    assert base.end_source == "none"


# ── Evidence ids ────────────────────────────────────────────────────────────


def _mixed_payloads():
    chat_payload = chat(
        {"id": "m-b", "created_at": at(300)},
        {"id": "m-a", "created_at": at(300)},
        {"id": "m-c", "created_at": at(-60)},
        {"id": "m-d", "created_at": None},
        {"id": "m-e", "created_at": at(10)},
    )
    questions_payload = questions(
        {"id": "q-b", "created_at": at(200), "response": "First answer.", "responded_at": at(400)},
        {"id": "q-a", "created_at": at(200), "response": "Second answer.", "responded_at": at(900)},
        {"id": "q-c", "created_at": at(100)},
    )
    return chat_payload, questions_payload


def test_chat_ids_follow_created_at_then_livestorm_id():
    chat_payload, questions_payload = _mixed_payloads()
    events, _ = bins(chat_payload, questions_payload)
    chat_rows = events[events["kind"] == "chat"]
    assert list(zip(chat_rows["eid"], chat_rows["livestorm_id"], strict=True)) == [
        ("C0001", "m-c"),
        ("C0002", "m-e"),
        ("C0003", "m-a"),  # same second as m-b: the Livestorm id breaks the tie
        ("C0004", "m-b"),
        ("C0005", "m-d"),  # no readable time: last
    ]


def test_question_and_answer_ids():
    chat_payload, questions_payload = _mixed_payloads()
    events, _ = bins(chat_payload, questions_payload)
    rows = events[events["kind"] != "chat"]
    assert list(zip(rows["eid"], rows["kind"], rows["livestorm_id"], rows["reply_to"], strict=True)) == [
        ("Q001", "question", "q-c", None),
        ("Q002", "question", "q-a", None),
        ("Q003", "question", "q-b", None),
        ("A001", "answer", "q-b", "Q003"),  # asked after q-a, answered first
        ("A002", "answer", "q-a", "Q002"),
    ]
    answers = rows[rows["kind"] == "answer"]
    assert answers["t"].tolist() == [400, 900]
    assert answers["is_team"].all()


def test_ids_are_stable_across_two_parses_and_payload_order():
    chat_payload, questions_payload = _mixed_payloads()
    first, _ = bins(chat_payload, questions_payload)
    second, _ = bins(copy.deepcopy(chat_payload), copy.deepcopy(questions_payload))
    pd.testing.assert_frame_equal(first, second)

    shuffled_chat = copy.deepcopy(chat_payload)
    shuffled_questions = copy.deepcopy(questions_payload)
    random.Random(7).shuffle(shuffled_chat["data"])
    random.Random(7).shuffle(shuffled_questions["data"])
    shuffled, _ = bins(shuffled_chat, shuffled_questions)
    pd.testing.assert_frame_equal(first, shuffled)


def test_ids_do_not_depend_on_the_clock():
    chat_payload, questions_payload = _mixed_payloads()
    with_clock, _ = bins(chat_payload, questions_payload)
    without_clock, _ = bins(chat_payload, questions_payload, session_payload=session())
    assert with_clock["eid"].tolist() == without_clock["eid"].tolist()
    assert with_clock["livestorm_id"].tolist() == without_clock["livestorm_id"].tolist()


def test_records_without_an_id_at_the_same_time_are_ordered_by_text():
    messages = [{"id": None, "created_at": at(50), "text_content": "B"}, {"id": None, "created_at": at(50), "text_content": "A"}]
    forward, _ = bins(chat(*messages))
    backward, _ = bins(chat(*reversed(messages)))
    assert forward["text"].tolist() == ["A", "B"]
    assert backward["text"].tolist() == ["A", "B"]
    assert forward["eid"].tolist() == ["C0001", "C0002"]


def test_a_record_repeated_by_pagination_counts_once():
    events, _ = bins(chat({"id": "m1", "created_at": at(5)}, {"id": "m1", "created_at": at(5)}, {"id": "m2", "created_at": at(6)}))
    assert events["livestorm_id"].tolist() == ["m1", "m2"]


def test_has_link_reads_the_text_and_the_html_href():
    events, _ = bins(
        chat(
            {"text_content": "Slides: https://example.com/deck"},
            {"text_content": "see www.example.com"},
            {"text_content": "the deck", "html_content": '<p>the <a href="https://example.com/deck">deck</a></p>'},
            {"text_content": "", "html_content": "<p>only <a href='https://example.com'>html</a> &amp; more</p>"},
            {"text_content": "no link here, just example.com", "html_content": "<p>no link here</p>"},
            {"text_content": "an anchor", "html_content": '<a href="#top">top</a>'},
        )
    )
    assert events["has_link"].tolist() == [True, True, True, True, False, False]
    assert events.loc[3, "text"] == "only html & more"


def test_is_team_comes_from_the_message_flags_without_authors():
    chat_payload = chat(
        {"author_id": "host", "from_team_member": True},
        {"author_id": "guest", "from_guest_speaker": "true"},
        {"author_id": "viewer"},
        {"author_id": "moderator"},
    )
    questions_payload = questions({"question_author_id": "moderator", "response": "Yes", "responded_at": at(60)})
    events = evidence.audience_events(chat_payload, questions_payload, clock.clock_base(one_hour_session()))
    assert events["is_team"].tolist() == [True, True, False, False, False, True]
    assert events["author_ref"].isna().all()
    # Resolving authors with the roster: tests/test_authors.py.


def test_empty_payloads_give_an_empty_table_with_every_column():
    for chat_payload, questions_payload in [(None, None), ({}, {}), ({"data": []}, {"data": []})]:
        events, series = bins(chat_payload, questions_payload)
        assert events.empty
        assert list(events.columns) == list(evidence.EVIDENCE_COLUMNS)
        assert len(series.minutes) == 60
        assert series.minutes[["audience_chat", "questions"]].to_numpy().sum() == 0
        assert series.unplaced == 0


# ── The payload shapes script ───────────────────────────────────────────────


def _shapes_script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "diagnose_payload_shapes.py"
    spec = importlib.util.spec_from_file_location("diagnose_payload_shapes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "url, local",
    [
        ("postgresql://u:p@localhost:5433/db", True),
        ("postgresql://u:p@127.0.0.1:5433/db?sslmode=disable", True),
        ("postgresql://u:p@db.example.com:5432/db", False),
        ("postgresql://u:p@localhost:5433/db?host=db.example.com", False),
        ("postgresql://u:p@localhost:5433/db?hostaddr=192.0.2.10", False),
        ("postgresql://u:p@localhost,db.example.com:5433/db", False),
        ("postgresql://u:p@localhost:5433/db?service=remote", False),
        ("postgresql:///db", False),
        ("host=localhost port=5433 dbname=db", True),
        ("host=localhost hostaddr=192.0.2.10 dbname=db", False),
    ],
)
def test_the_shapes_script_checks_the_host_libpq_will_use(monkeypatch, url, local):
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)
    assert _shapes_script().is_local_database(url) is local


def test_the_shapes_script_refuses_a_remote_hostaddr_from_the_environment(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.10")
    assert _shapes_script().is_local_database("postgresql://u:p@localhost:5433/db") is False


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "empty"),
        ("  ", "empty"),
        ("https://app.livestorm.co/p/abc", "Livestorm host"),
        ("https://example.com/page", "other host"),
        ("newsletter", "not a URL"),
        ("http://[::1", "not a URL"),
    ],
)
def test_the_shapes_script_classes_referrers_without_failing(value, expected):
    assert _shapes_script().referrer_class(value) == expected


def test_the_shapes_script_checks_chat_authors_by_their_people_link(capsys):
    people = [{"id": person_id, "type": "people", "attributes": {}} for person_id in ("p1", "p2")]
    session_payload = {**one_hour_session(), "included": people}
    chat_payload = chat(
        {"author_id": "c-1"},
        {"author_id": "c-2"},
        {"author_id": "p2"},
    )
    chat_payload["data"][0]["relationships"] = {"people": {"data": {"type": "people", "id": "p1"}}}
    chat_payload["data"][1]["relationships"] = {"people": {"data": {"type": "people", "id": "p9"}}}

    _shapes_script().check_rows({"s1": {"session_payload": session_payload, "chat_payload": chat_payload}})

    output = capsys.readouterr().out

    def counts(heading):
        lines = output.split(f"== {heading} ==")[1].split("==")[0].strip().splitlines()
        return dict(line.strip().rsplit(": ", 1) for line in lines)

    assert counts("Chat authors (people link) vs the session's people") == {
        "in session people": "1",
        "not in session people": "1",
        "no people link (cached before include=people)": "1",
    }
    assert counts("Check: chat author_id is never a people id") == {"not a people id": "2", "equals a people id": "1"}
