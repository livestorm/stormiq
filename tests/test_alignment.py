"""The alignment probe and its one gate (`livestorm_app/record/alignment.py`).

Synthetic transcripts and chat at known offsets: a recording that started
`offset` seconds after `started_at` says a line at recording second `r`
at session second `r + offset`. Covers the anchors (link, wave, read aloud,
media time), one anchor per message and utterance, the duration constraint
and the search bounds, the sweep, the drift check, the statuses and their
reasons, English and French cues, one-to-one matching, `alignment_allows`,
what the record inputs pass the probe, and the study script's local-only,
text-free output. Pure functions: no database, Redis or network. Every
message and line below is made up.
"""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import synthetic_session as synthetic
from livestorm_app.record import alignment, clock, evidence
from livestorm_app.record.alignment import (
    ASSUMED,
    AUDIENCE,
    LINK,
    MEDIA_TIME,
    MINUTES,
    MISALIGNED,
    READ_ALOUD,
    SECONDS,
    UNAVAILABLE,
    VERIFIED,
    WAVE,
    alignment_allows,
    content_tokens,
    fold_tokens,
    probe_alignment,
    sweep,
)
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.transcript import parse_transcript

T0 = int(datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc).timestamp())
HOUR = 3600
WORD_S = 0.4  # each word starts this long after the one before, and lasts 0.3 s
FILLER = "we walk through each view step by step and show the main screens"

# The scripted lines of `scene`, at recording seconds after its start.
LINK_LINE = "i just dropped the link to the slides"  # "the link" starts at word 3
WAVE_LINE = "tell us in the chat where you are joining from"  # the call ends with "from", word 9
EXPORT_QUESTION = "Does the export handle large spreadsheets with formulas and charts?"
EXPORT_READ = "next question does the export handle large spreadsheets with formulas and charts"  # "export" is word 4
CALENDAR_QUESTION = "Can the calendar sync invite reminders across several regions automatically?"
CALENDAR_READ = "someone asks can the calendar sync invite reminders across several regions automatically"  # "calendar" is word 4


# ── Synthetic sessions ──────────────────────────────────────────────────────


def line(start, text, speaker=0):
    words = text.split()
    timed = [
        {"word": f" {word}", "start": round(start + index * WORD_S, 2), "end": round(start + index * WORD_S + 0.3, 2), "confidence": 0.9}
        for index, word in enumerate(words)
    ]
    return {"start": timed[0]["start"], "end": timed[-1]["end"], "speaker": speaker, "language": "en", "text": text, "words": timed}


def word_start(start, index):
    return round(start + index * WORD_S, 2)


def word_end(start, index):
    return round(start + index * WORD_S + 0.3, 2)


def recording(*lines, audio_s=HOUR, filler=True, media=None):
    """A Gladia transcript of `lines`, with filler talk every 30 seconds
    between them (never within 15 seconds of a scripted line)."""
    utterances = list(lines)
    if filler:
        starts = [item["start"] for item in lines]
        for start in range(5, int(audio_s) - 10, 30):
            if all(abs(start - other) >= 15 for other in starts):
                utterances.append(line(float(start), FILLER, speaker=1))
    payload = {
        "result": {
            "metadata": {"audio_duration": float(audio_s)},
            "transcription": {"languages": ["en"], "utterances": sorted(utterances, key=lambda item: item["start"])},
        }
    }
    if media is not None:
        payload["stormiq"] = {"media": {"duration_s": float(audio_s), "creation_time": media}}
    return payload


def session(length=HOUR, **attributes):
    values = {"started_at": T0, "ended_at": T0 + length, "duration": length, "estimated_started_at": T0}
    values.update(attributes)
    return {"data": {"id": "s1", "type": "sessions", "attributes": values}}


def message(at, text, *, team=False):
    return {"at": at, "text": text, "team": team}


def chat(*messages):
    data = []
    for index, item in enumerate(sorted(messages, key=lambda m: m["at"]), start=1):
        data.append(
            {
                "id": f"m-{index:03d}",
                "type": "chat_messages",
                "attributes": {
                    "text_content": item["text"],
                    "created_at": T0 + item["at"],
                    "author_id": f"a-{index}",
                    "from_team_member": item["team"],
                    "from_guest_speaker": False,
                },
            }
        )
    return {"data": data}


def question(at, text, *, oral=False, responded=None):
    return {"at": at, "text": text, "oral": oral, "responded": responded}


def questions(*items):
    data = []
    for index, item in enumerate(items, start=1):
        data.append(
            {
                "id": f"q-{index:03d}",
                "type": "questions",
                "attributes": {
                    "question": item["text"],
                    "created_at": T0 + item["at"],
                    "responded_at": T0 + item["responded"] if item["responded"] is not None else None,
                    "response": None,
                    "response_orally": item["oral"],
                    "question_author_id": f"asker-{index}",
                },
            }
        )
    return {"data": data}


def probe(transcript_payload, chat_payload=None, questions_payload=None, *, session_payload=None, internal=False):
    base = clock.clock_base(session_payload or session())
    events = evidence.audience_events(chat_payload, questions_payload, base)
    return probe_alignment(
        parse_transcript(transcript_payload), events, base, questions_payload=questions_payload, internal=internal
    )


def scene(
    offset, start=100.0, *, link=True, wave=True, read=True, read_line=EXPORT_READ, asked=EXPORT_QUESTION,
    link_lag=5, burst_lag=8, read_lag=12,
):
    """Lines, chat and questions for a recording that started `offset`
    seconds after `started_at`, beginning at recording second `start`:

    - the host announces a link (`start`), posted `link_lag` (5) s after
      "the link" is said;
    - calls the room to the chat (`start + 100`): four audience messages
      start `burst_lag` (8) s after the call ends;
    - reads a question aloud (`start + 300`), `read_lag` (12) s after it was asked.

    With the default lags, these allow [offset − 85, offset + 35] (link),
    [offset − 37, offset + 5] (wave) and [offset − 9, offset + 1788] (read)
    on the session clock."""
    lines, messages, asks = [], [], []
    if link:
        lines.append(line(start, LINK_LINE))
        messages.append(message(word_start(start, 3) + offset + link_lag, "Slides: https://example.test/slides", team=True))
    if wave:
        lines.append(line(start + 100, WAVE_LINE))
        burst = word_end(start + 100, 9) + offset + burst_lag
        messages += [message(burst + lag, text) for lag, text in ((0, "Hi from Lyon"), (2, "Berlin here"), (5, "Hello from Porto"), (9, "Hi all"))]
    if read:
        lines.append(line(start + 300, read_line))
        asks.append(question(word_start(start + 300, 4) + offset - read_lag, asked))
    return lines, messages, asks


def link_at(r, lag):
    """A link announced at recording second `r` and posted `lag` s after
    "the link" is said (offset 0): [lag − 90, lag + 30]."""
    return [line(r, LINK_LINE)], [message(word_start(r, 3) + lag, "Deck: https://example.test/deck", team=True)], []


def wave_at(r, lag):
    """A call to the chat at recording second `r`, answered by a burst
    `lag` s after the call ends (offset 0): [lag − 45, lag − 3]."""
    burst = word_end(r, 9) + lag
    return [line(r, WAVE_LINE)], [message(burst + step, "Hi there") for step in (0, 2, 4)], []


def scene_probe(*scenes, audio_s=HOUR, **options):
    lines = [item for scene_lines, _messages, _asks in scenes for item in scene_lines]
    messages = [item for _lines, scene_messages, _asks in scenes for item in scene_messages]
    asks = [item for _lines, _messages, scene_asks in scenes for item in scene_asks]
    return probe(recording(*lines, audio_s=audio_s), chat(*messages), questions(*asks), **options)


def kinds(result):
    return [anchor.kind for anchor in result.anchors]


# ── Status ──────────────────────────────────────────────────────────────────


def test_a_recording_40_seconds_late_is_verified_by_its_link_wave_and_read_aloud():
    result = scene_probe(scene(40), audio_s=HOUR - 40)
    assert result.status == VERIFIED and result.reasons == ()
    assert sorted(kinds(result)) == [LINK, READ_ALOUD, WAVE]
    assert all(anchor.agrees for anchor in result.anchors)
    assert abs(result.offset_s - 40) <= 15 and result.plusminus_s <= 15
    assert (result.estimate.lo, result.estimate.hi) == pytest.approx((31, 45), abs=0.05)
    assert result.offset_s == pytest.approx(38, abs=0.05), "0 is outside the agreed range: its middle"
    assert alignment_allows(result, SECONDS) and alignment_allows(result, MINUTES)


def test_a_recording_on_time_is_verified_at_0():
    """0 inside the agreed range keeps the hypothesis that the recording starts at `started_at`."""
    result = scene_probe(scene(0))
    assert (result.status, result.offset_s) == (VERIFIED, 0)
    assert result.plusminus_s == pytest.approx(9, abs=0.05)


def test_verified_needs_the_offset_within_15_seconds_not_just_a_narrow_range():
    """A link, a burst 29 s after the call and a question read 6 s after it
    was asked agree on [−3, 26]: under 30 s wide, but 0 lies in it, so the
    offset applied would be 0 ± 26. Not verified: the clock is assumed."""
    result = scene_probe(scene(0, burst_lag=29, read_lag=6))
    assert (result.estimate.lo, result.estimate.hi) == pytest.approx((-3, 26), abs=0.05)
    assert result.estimate.width <= 30 and result.estimate.plusminus == pytest.approx(26, abs=0.05)
    assert (result.status, result.offset_s, result.reasons) == (ASSUMED, 0, ("wide_range",))
    assert result.to_json()["allows"] == {"seconds": False, "minutes": True}


def test_no_anchors_and_matching_durations_assume_offset_0():
    result = probe(recording(audio_s=HOUR - 20))
    assert (result.status, result.offset_s, result.plusminus_s, result.n_anchors) == (ASSUMED, 0, None, 0)
    assert result.reasons == ("no_anchors",)
    assert not alignment_allows(result, SECONDS) and alignment_allows(result, MINUTES)


def test_a_ten_minute_gap_without_anchors_is_misaligned():
    result = probe(recording(audio_s=HOUR - 600))
    assert (result.status, result.offset_s, result.reasons) == (MISALIGNED, None, ("duration_gap",))
    assert result.durations["gapS"] == 600 and (result.durations["lo"], result.durations["hi"]) == (-30, 630)
    assert not alignment_allows(result, MINUTES)


@pytest.mark.parametrize("gap, status", [(50, ASSUMED), (70, MISALIGNED)])
def test_lengths_agree_within_60_seconds_on_a_short_session(gap, status):
    """On a 30-minute session, 2% is 36 s: the 60 s floor decides."""
    result = probe(recording(audio_s=1800 - gap), session_payload=session(1800))
    assert result.status == status
    assert result.reasons == (("no_anchors",) if status == ASSUMED else ("duration_gap",))


def test_a_recording_without_a_length_is_misaligned():
    payload = recording()
    payload["result"]["metadata"] = {}
    result = probe(payload)
    assert result.durations["audioS"] is None and result.durations["lo"] is None
    assert (result.status, result.offset_s, result.reasons) == (MISALIGNED, None, ("duration_unknown",))


def test_anchors_outside_the_duration_constraint_do_not_agree():
    """The anchors point at +100, but a recording as long as the session
    must start within 30 s of `started_at`: the constraint leaves one
    anchor of three, so they disagree and nothing is verified."""
    result = scene_probe(scene(100), audio_s=HOUR)
    assert (result.durations["lo"], result.durations["hi"]) == (-30, 30)
    assert (result.status, result.offset_s, result.reasons) == (MISALIGNED, None, ("anchors_disagree",))
    assert (result.n_agree, result.n_anchors) == (1, 3)


def test_three_anchors_of_five_agreeing_is_weak_agreement():
    """Two links point at +50 to +170, away from the three anchors that agree
    on 0 (and outside the duration constraint): 60% agree, under 75%."""
    result = scene_probe(scene(0), link_at(2000.0, 140), link_at(2500.0, 140))
    assert (result.n_agree, result.n_anchors) == (3, 5)
    assert (result.status, result.offset_s, result.reasons) == (ASSUMED, 0, ("weak_agreement",))
    assert result.halves["late"] is None, "the late links agree on 120 s: too wide to be bounded"


def test_anchors_can_verify_an_offset_the_durations_disagree_with():
    """A recording started ten minutes in: the durations disagree, and the anchors verify +600."""
    result = scene_probe(scene(600), audio_s=HOUR - 600)
    assert result.status == VERIFIED and abs(result.offset_s - 600) <= 15


def test_read_alouds_alone_bound_one_side_only():
    """Chat, then speech: lower bounds only. At most assumed."""
    result = scene_probe(scene(0, link=False, wave=False), scene(0, start=1000, link=False, wave=False, read_line=CALENDAR_READ, asked=CALENDAR_QUESTION))
    assert kinds(result) == [READ_ALOUD, READ_ALOUD]
    assert result.status == ASSUMED and result.offset_s == 0
    assert "one_direction" in result.reasons and "wide_range" in result.reasons


def test_waves_alone_bound_one_side_only_even_when_narrow():
    """Speech, then chat: two waves agree on a range under 30 s wide, but
    the reaction lag bounds the other side, so it stays assumed."""
    first = scene(0, link=False, read=False)
    lines, messages, _asks = scene(0, start=1000, link=False, read=False)
    late = [message(item["at"] + 30, item["text"]) for item in messages]  # the room answers 38 s after the call
    result = scene_probe(first, (lines, late, []))
    assert kinds(result) == [WAVE, WAVE] and result.n_agree == 2
    assert result.estimate.plusminus <= alignment.VERIFIED_MAX_PLUSMINUS_S
    assert (result.status, result.reasons) == (ASSUMED, ("one_direction",))


def test_anchors_that_exclude_0_without_verifying_are_misaligned():
    """A link and a read aloud agree on about +200, but only loosely (no wave)."""
    result = scene_probe(scene(200, wave=False), audio_s=HOUR - 200)
    assert result.status == MISALIGNED
    assert "offset_not_zero" in result.reasons and "duration_gap" in result.reasons


def test_an_offset_that_changes_halfway_is_drift():
    """0 in the first half of the recording, +90 in the second (a pause cut
    from the recording): misaligned, whatever the first half says."""
    early = scene(0)
    late = scene(90, start=2000, read_line=CALENDAR_READ, asked=CALENDAR_QUESTION)
    result = scene_probe(early, late)
    assert [anchor.half for anchor in result.anchors] == ["early"] * 3 + ["late"] * 3
    assert result.halves["early"].choice == 0
    assert result.halves["late"].choice == pytest.approx(88, abs=0.05)
    assert (result.status, result.offset_s, result.reasons) == (MISALIGNED, None, ("drift",))
    assert result.to_json()["drift"]["late"]["offsetS"] == 88


def test_halves_that_agree_are_not_drift():
    result = scene_probe(scene(20), scene(20, start=2000, read_line=CALENDAR_READ, asked=CALENDAR_QUESTION), audio_s=HOUR - 20)
    assert result.status == VERIFIED and result.n_agree == 6


def test_halves_whose_ranges_overlap_are_not_drift():
    """The early half allows −9 to +35 (its offset alone would be 0), the
    late one +26 to +40: one offset fits both, so no drift. Verified."""
    early = scene(0, wave=False)
    late = scene(35, start=2000, read_line=CALENDAR_READ, asked=CALENDAR_QUESTION)
    result = scene_probe(early, late, audio_s=HOUR - 35)
    halves = result.halves
    assert (halves["early"].lo, halves["early"].hi, halves["early"].choice) == pytest.approx((-9, 35, 0), abs=0.05)
    assert (halves["late"].lo, halves["late"].hi) == pytest.approx((26, 40), abs=0.05)
    assert (result.status, result.reasons, result.n_agree) == (VERIFIED, (), 5)
    assert result.offset_s == pytest.approx(30.5, abs=0.05)


def test_drift_alone_blocks_verified():
    """Every other rule passes (4 of 5 anchors agree on −9 to +5, from both
    sides), but the late half alone agrees on +40 to +82, 35 s away from
    the early half's range. The early anchors sit past a quarter of the
    recording, still in its first half."""
    result = scene_probe(scene(0, start=1200.0), link_at(2000.0, 60), wave_at(2100.0, 85))
    assert [anchor.half for anchor in result.anchors] == ["early"] * 3 + ["late"] * 2
    assert (result.n_agree, result.n_anchors, result.estimate.plusminus) == (4, 5, pytest.approx(9, abs=0.05))
    assert (result.halves["late"].lo, result.halves["late"].hi) == pytest.approx((40, 82), abs=0.05)
    assert (result.status, result.offset_s, result.reasons) == (MISALIGNED, None, ("drift",))


def test_a_half_with_one_anchor_is_not_bounded():
    """One far wave in the late half: it can't show drift on its own."""
    result = scene_probe(scene(0), wave_at(2100.0, 85))
    assert (result.n_agree, result.n_anchors) == (3, 4)
    assert result.halves["late"] is None
    assert (result.status, result.reasons) == (VERIFIED, ())


@pytest.mark.parametrize(
    "transcript_payload, session_payload, reasons",
    [
        (None, session(), ["no_transcript"]),
        (recording(), session(started_at=None), ["no_clock"]),
        (None, session(started_at=None), ["no_transcript", "no_clock"]),
    ],
    ids=["no transcript", "no started_at", "neither"],
)
def test_no_transcript_or_no_clock_is_unavailable(transcript_payload, session_payload, reasons):
    base = clock.clock_base(session_payload)
    result = probe_alignment(parse_transcript(transcript_payload), None, base)
    assert (result.status, result.offset_s, list(result.reasons), result.anchors) == (UNAVAILABLE, None, reasons, ())
    assert not alignment_allows(result, MINUTES) and alignment_allows(result, AUDIENCE)


# ── Anchors ─────────────────────────────────────────────────────────────────


def test_the_ranges_of_each_anchor():
    result = scene_probe(scene(40), audio_s=HOUR - 40)
    by_kind = {anchor.kind: anchor for anchor in result.anchors}
    link, wave, read = by_kind[LINK], by_kind[WAVE], by_kind[READ_ALOUD]
    assert (link.r, link.lo, link.hi) == pytest.approx((101.2, -45, 75), abs=0.01)
    assert (wave.r, wave.lo, wave.hi, wave.burst) == pytest.approx((203.9, 3, 45, 4), abs=0.01)
    assert (read.r, read.lo, read.hi) == pytest.approx((401.6, 31, 1828), abs=0.01)
    assert (link.e, wave.e, read.e) == ("C0001", "C0002", "Q001")
    assert (link.u, wave.u, read.u) == (("U0004",), ("U0008",), ("U0015",))


def test_french_cues_and_accents():
    """"Je vous mets le lien dans le chat", "bonjour à tous, dites-nous dans
    le chat", and a question read aloud without its accents."""
    lines = [
        line(100.0, "je vous mets le lien dans le chat"),
        line(200.0, "bonjour à tous dites-nous dans le chat d'où vous venez"),
        line(400.0, "alors est-ce que l'integration salesforce fonctionne deja avec les comptes hubspot"),
    ]
    offset = 25
    burst = word_end(200.0, 6) + offset + 6  # "chat" is word 6 once "dites-nous" is one word
    messages = [message(100.0 + offset + 4, "Le lien : https://example.test/demo", team=True)]
    messages += [message(burst + lag, "Bonjour de Lyon") for lag in (0, 3, 7)]
    asked = question(word_start(400.0, 4) + offset - 8, "Est-ce que l'intégration Salesforce fonctionne déjà avec les comptes HubSpot ?")
    result = probe(recording(*lines, audio_s=HOUR - offset), chat(*messages), questions(asked))
    assert sorted(kinds(result)) == [LINK, READ_ALOUD, WAVE]
    link = next(anchor for anchor in result.anchors if anchor.kind == LINK)
    assert link.r == 100.0, "the first cue of the line: je vous mets"
    assert result.status == VERIFIED and abs(result.offset_s - offset) <= 15


def test_folding_and_content_words():
    assert fold_tokens("Dites-nous où, l'intégration ÇA") == ("dites", "nous", "ou", "l", "integration", "ca")
    assert content_tokens("How does the pricing work for larger teams, larger teams?") == ["pricing", "larger", "teams"]
    assert content_tokens("Est-ce que vous avez 2026 comptes ?") == ["avez", "comptes"]


def test_one_link_message_per_cue_and_one_cue_per_message():
    """Two links posted after one announcement: one anchor. One link and two
    announcements: one anchor, with the nearer announcement."""
    lines = [line(100.0, LINK_LINE)]
    two_messages = chat(message(110, "https://example.test/a", team=True), message(130, "https://example.test/b", team=True))
    result = probe(recording(*lines), two_messages)
    assert kinds(result) == [LINK]

    lines = [line(100.0, LINK_LINE), line(600.0, "here is the link again for everyone")]
    result = probe(recording(*lines), chat(message(108, "https://example.test/a", team=True)))
    assert kinds(result) == [LINK] and result.anchors[0].r == 101.2


def test_a_link_needs_a_team_message_with_a_link():
    lines = [line(100.0, LINK_LINE)]
    assert probe(recording(*lines), chat(message(108, "https://example.test/a"))).anchors == ()
    assert probe(recording(*lines), chat(message(108, "slides coming", team=True))).anchors == ()


def test_a_call_to_the_chat_is_not_a_link_cue():
    """"Tell us in the chat" holds "in the chat", but it is a wave's cue."""
    lines, messages, _asks = scene(0, link=False, read=False)
    messages.append(message(205, "https://example.test/map", team=True))
    result = probe(recording(*lines), chat(*messages))
    assert kinds(result) == [WAVE]


def test_a_burst_needs_three_audience_messages_within_20_seconds():
    lines = [line(100.0, WAVE_LINE)]
    end = word_end(100.0, 9)
    two = chat(message(end + 8, "Hi"), message(end + 10, "Hello"), message(end + 40, "Hey"))
    team = chat(message(end + 8, "Hi", team=True), message(end + 9, "Hi", team=True), message(end + 10, "Hi", team=True))
    assert probe(recording(*lines), two).anchors == ()
    assert probe(recording(*lines), team).anchors == ()
    assert kinds(probe(recording(*lines), team, internal=True)) == [WAVE], "on an internal session everyone is audience"


def test_a_burst_must_beat_a_busy_chat():
    """With about 2 messages per 20 s all session long, a burst needs 3 × that."""
    lines = [line(100.0, WAVE_LINE)]
    end = word_end(100.0, 9)
    steady = [message(at, "chatting") for at in range(0, HOUR, 10)]
    burst = [message(end + 8 + lag, "Hi") for lag in (0, 1, 2)]
    assert WAVE not in kinds(probe(recording(*lines), chat(*steady, *burst)))
    bigger = [message(end + 8 + lag, "Hi") for lag in range(8)]
    assert WAVE in kinds(probe(recording(*lines), chat(*steady, *bigger)))


def test_a_read_aloud_needs_five_content_words():
    lines = [line(400.0, "next question can we export to csv please")]
    assert probe(recording(*lines), None, questions(question(390, "Can we export to CSV?"))).anchors == ()


def test_a_read_aloud_must_be_unique():
    """The same words in two separate places: no anchor."""
    lines = [line(400.0, EXPORT_READ), line(900.0, EXPORT_READ)]
    assert probe(recording(*lines), None, questions(question(390, EXPORT_QUESTION))).anchors == ()


def test_a_read_aloud_must_keep_the_order():
    scrambled = "charts and formulas with spreadsheets large handle export the does"
    assert probe(recording(line(400.0, scrambled)), None, questions(question(390, EXPORT_QUESTION))).anchors == ()


@pytest.mark.parametrize(
    "said, found",
    [
        ("they ask can the scheduler export weekly attendance reports into finance dashboards", True),  # 8 of 10
        ("they ask can the scheduler export weekly attendance reports and finance", False),  # 6 of 10, in order
    ],
)
def test_a_read_aloud_needs_70_percent_of_its_words(said, found):
    """Recall decides here: the 6 words said are in order (60%, enough for
    the order rule), but 6 of 10 is under 70%."""
    asked = "Can the scheduler export weekly attendance reports into shared finance dashboards automatically tonight?"
    result = probe(recording(line(400.0, said)), None, questions(question(390, asked)))
    assert kinds(result) == ([READ_ALOUD] if found else [])


def test_a_read_aloud_may_span_three_utterances():
    lines = [
        line(400.0, "next question does the export"),
        line(402.5, "handle large spreadsheets"),
        line(404.0, "with formulas and charts"),
    ]
    result = probe(recording(*lines), None, questions(question(390, EXPORT_QUESTION)))
    assert kinds(result) == [READ_ALOUD]
    assert len(result.anchors[0].u) == 3 and result.anchors[0].r == word_start(400.0, 4)


def test_team_chat_is_never_read_aloud_and_a_team_question_only_on_an_internal_session():
    """The one typing team chat may be the one speaking, and type the words
    after saying them, so it never bounds the offset from below, even on an
    internal session. A question is asked before it is read: there, a team
    member's question counts."""
    lines = [line(400.0, EXPORT_READ)]
    team_chat = chat(message(390, EXPORT_QUESTION, team=True))
    assert probe(recording(*lines), team_chat).anchors == ()
    assert probe(recording(*lines), team_chat, internal=True).anchors == ()

    base = clock.clock_base(session())
    asked = questions(question(390, EXPORT_QUESTION))
    events = evidence.audience_events(None, asked, base).assign(is_team=True)  # asked by a team member
    transcript = parse_transcript(recording(*lines))
    assert probe_alignment(transcript, events, base, questions_payload=asked).anchors == ()
    assert kinds(probe_alignment(transcript, events, base, questions_payload=asked, internal=True)) == [READ_ALOUD]


def test_a_link_said_then_posted_on_an_internal_session_does_not_confirm_itself():
    """The host says what they will post, then posts the same words with a
    link. On an internal session everyone is audience, but the message is
    team chat: one link anchor, never a read aloud of it as well."""
    lines = [line(100.0, "i will put the link to the onboarding checklist for new managers and team leads in the chat")]
    posted = chat(message(125, "Onboarding checklist for new managers and team leads: https://example.test/a", team=True))
    for internal in (False, True):
        result = probe(recording(*lines), posted, internal=internal)
        assert [(anchor.kind, anchor.e) for anchor in result.anchors] == [(LINK, "C0001")]
        assert (result.status, result.reasons) == (ASSUMED, ("few_anchors", "wide_range"))


def test_one_utterance_or_message_feeds_one_anchor():
    """A link cue in the line that reads a question aloud: the link keeps the
    utterance. A burst's first message read aloud: the wave keeps the message."""
    read_line = "next question is there a link to the onboarding checklist for new managers and team leads"
    asked = questions(question(390, "Is there a link to the onboarding checklist for new managers and team leads?"))
    posted = chat(message(410, "Checklist: https://example.test/checklist", team=True))
    result = probe(recording(line(400.0, read_line)), posted, asked)
    assert [(anchor.kind, anchor.e, anchor.u) for anchor in result.anchors] == [(LINK, "C0001", ("U0014",))]

    greeting = "Hello from Lyon, so glad to join this onboarding session today"
    end = word_end(100.0, 9)
    room = chat(message(end + 8, greeting), message(end + 10, "Hi"), message(end + 12, "Hello"))
    lines = [line(100.0, WAVE_LINE), line(160.0, "hello from lyon so glad to join this onboarding session today")]
    result = probe(recording(*lines), room)
    assert [(anchor.kind, anchor.e) for anchor in result.anchors] == [(WAVE, "C0001")]


def test_a_question_answered_aloud_bounds_the_read_from_above_too():
    lines = [line(400.0, EXPORT_READ)]
    r_read = word_start(400.0, 4)
    plain = probe(recording(*lines), None, questions(question(390, EXPORT_QUESTION)))
    answered = probe(recording(*lines), None, questions(question(390, EXPORT_QUESTION, oral=True, responded=420)))
    assert plain.anchors[0].hi == pytest.approx(390 - r_read + 1800)
    assert answered.anchors[0].hi == pytest.approx(420 - r_read + 300)
    assert answered.anchors[0].lo == plain.anchors[0].lo


def test_one_place_read_for_two_messages_counts_once_for_the_earlier():
    lines = [line(400.0, EXPORT_READ)]
    both = chat(message(380, EXPORT_QUESTION), message(392, EXPORT_QUESTION))
    result = probe(recording(*lines), both)
    assert [(anchor.kind, anchor.e) for anchor in result.anchors] == [(READ_ALOUD, "C0001")]


def test_the_media_time_anchor_stays_off_until_confirmed(monkeypatch):
    created = datetime.fromtimestamp(T0 + 30, tz=timezone.utc).isoformat()
    payload = recording(audio_s=HOUR - 30, media=created)
    assert alignment.MEDIA_TIME_ANCHOR is False
    assert probe(payload).anchors == ()
    monkeypatch.setattr(alignment, "MEDIA_TIME_ANCHOR", True)
    result = probe(payload)
    assert kinds(result) == [MEDIA_TIME]
    assert (result.anchors[0].lo, result.anchors[0].hi, result.anchors[0].half) == (28, 32, None)
    # Tight and from both sides, but alone: never verified.
    assert (result.status, result.offset_s, result.reasons) == (ASSUMED, 0, ("few_anchors",))


def test_a_pair_outside_the_search_bounds_is_never_made():
    """Posted 500 s after it was announced: made (and outside the duration
    constraint, so it doesn't agree). 1,000 s after: outside ±900 s, never
    made, unless the lengths differ by 1,000 s, which widens the bounds to
    1,120 s."""
    assert kinds(scene_probe(link_at(100.0, 500))) == [LINK]
    assert scene_probe(link_at(100.0, 1000)).anchors == ()
    widened = scene_probe(link_at(100.0, 1000), audio_s=HOUR - 1000)
    assert kinds(widened) == [LINK] and widened.anchors[0].lo == pytest.approx(910, abs=0.05)


# ── The sweep ───────────────────────────────────────────────────────────────


def test_the_sweep_finds_the_range_most_ranges_share():
    window = sweep([(-10, 20), (5, 40), (0, 12), (100, 120)], (-900, 900))
    assert (window.lo, window.hi, window.count, sorted(window.members)) == (5, 12, 3, [0, 1, 2])
    assert (window.choice, window.plusminus) == (8.5, 3.5)


def test_the_sweep_prefers_the_tied_range_nearest_0_and_clips_to_the_domain():
    window = sweep([(50, 60), (-40, -35), (1000, 1100)], (-900, 900))
    assert (window.lo, window.hi, window.count) == (-40, -35, 1)
    later = sweep([(-100, -90), (5, 10)], (-900, 900))
    assert (later.lo, later.hi) == (5, 10), "the nearest to 0, not the first"
    holding = sweep([(-50, -40), (-5, 5)], (-900, 900))
    assert (holding.lo, holding.hi, holding.choice) == (-5, 5, 0), "the one holding 0"
    assert sweep([(50, 60)], (-30, 30)) is None
    touching = sweep([(0, 10), (10, 20)], (-900, 900))
    assert (touching.lo, touching.hi, touching.count) == (10, 10, 2), "ranges are closed"


# ── The gate ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status, seconds, minutes",
    [(VERIFIED, True, True), (ASSUMED, False, True), (MISALIGNED, False, False), (UNAVAILABLE, False, False)],
)
def test_alignment_allows(status, seconds, minutes):
    result = probe(recording())
    result = alignment.Alignment(**{**vars(result), "status": status})
    for form in (result, result.to_json(), {"status": status}):
        assert alignment_allows(form, SECONDS) is seconds
        assert alignment_allows(form, MINUTES) is minutes
        assert alignment_allows(form, AUDIENCE) is True
    assert result.to_json()["allows"] == {"seconds": seconds, "minutes": minutes}


def test_alignment_allows_fails_closed():
    assert alignment_allows(None, MINUTES) is False and alignment_allows(None, AUDIENCE) is True
    assert alignment_allows({"status": "unknown"}, MINUTES) is False
    with pytest.raises(ValueError):
        alignment_allows({"status": VERIFIED}, "hours")


def test_only_the_gate_reads_the_status():
    """Outside `alignment.py`, record code never branches on a status."""
    record_dir = Path(alignment.__file__).resolve().parent
    pattern = re.compile(r"""\.status\b|\[["']status["']\]|get\(["']status["']|getattr\([^)]*["']status["']""")
    readers = [
        path.name
        for path in record_dir.glob("*.py")
        if path.name != "alignment.py" and pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert readers == ["measures.py"], "only measures.py copies it into its coverage"
    copies = re.findall(r"^.*\.status\b.*$", (record_dir / "measures.py").read_text(encoding="utf-8"), re.MULTILINE)
    assert [text.strip() for text in copies] == ['"status": alignment.status,']


# ── Output ──────────────────────────────────────────────────────────────────


def test_the_json_carries_codes_ids_and_times_only():
    payload = json.dumps(scene_probe(scene(40), audio_s=HOUR - 40).to_json())
    for text in ("Slides", "Lyon", "export", "spreadsheets", "joining", "https"):
        assert text not in payload
    strings = re.findall(r'"([^"]*)"', payload)
    assert all(re.fullmatch(r"[A-Za-z_]+|[CQU]\d{3,4}", value) for value in strings), strings


def test_the_synthetic_webinar_is_verified_from_the_record_inputs():
    """The probe runs once per load, from the parsed transcript and the events."""
    inputs = load_record_inputs(synthetic.cached_row())
    result = inputs.alignment
    assert result.status == VERIFIED
    assert (result.offset_s, result.plusminus_s) == pytest.approx((18.9, 5.7), abs=1e-6)
    assert sorted(kinds(result)) == [LINK, READ_ALOUD, WAVE]
    assert {anchor.e for anchor in result.anchors} <= set(inputs.events["eid"])
    assert {uid for anchor in result.anchors for uid in anchor.u} <= set(inputs.transcript.utterances["uid"])


def _all_hands(row):
    """Everyone a team member, every chat message from the team."""
    for person in row["session_payload"]["included"]:
        person["attributes"]["role"] = "team_member"
        person["attributes"]["registrant_detail"]["is_guest_speaker"] = False
    for item in row["chat_payload"]["data"]:
        item["attributes"]["from_team_member"] = True
    return row


def test_the_record_inputs_tell_the_probe_the_session_is_internal():
    """An all-hands: its burst is team chat and its question was asked by a
    team member, so the wave and the read aloud need the internal flag."""
    inputs = load_record_inputs(_all_hands(synthetic.cached_row()))
    assert inputs.roster.meta.internal is True
    assert sorted(kinds(inputs.alignment)) == [LINK, READ_ALOUD, WAVE]
    assert inputs.alignment.status == VERIFIED


def test_the_record_inputs_pass_the_questions_answered_aloud():
    """The question read aloud was answered aloud 20 s after it was asked:
    the read is bounded from above by `responded_at` too."""
    row = synthetic.cached_row()
    item = next(item for item in row["questions_payload"]["data"] if item["id"] == "qst-002")
    item["attributes"]["responded_at"] = synthetic.T0 + 320
    read = next(anchor for anchor in load_record_inputs(row).alignment.anchors if anchor.kind == READ_ALOUD)
    assert read.hi == pytest.approx(320 - read.r + 300)
    assert read.lo == pytest.approx(read.t - read.r + 3)


def test_the_probe_module_is_pure():
    source = Path(alignment.__file__).read_text(encoding="utf-8")
    for name in ("livestorm_app.db", "requests", "fastapi", "psycopg", "redis"):
        assert f"import {name}" not in source and f"from {name}" not in source


# ── The study script ────────────────────────────────────────────────────────


def _study_script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "probe_alignment_study.py"
    spec = importlib.util.spec_from_file_location("probe_alignment_study", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "url, local",
    [
        ("postgresql://u:p@localhost:5433/db", True),
        ("postgresql://u:p@db.example.com:5432/db", False),
        ("postgresql://u:p@localhost:5433/db?host=db.example.com", False),
    ],
)
def test_the_study_script_refuses_a_database_that_is_not_local(monkeypatch, url, local):
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)
    assert _study_script().is_local_database(url) is local


def _forbid_reads(monkeypatch, study):
    def refused(*args, **kwargs):
        raise AssertionError("the study script read the database before checking it is local")

    monkeypatch.setattr(study, "admin_list_sessions", refused)
    monkeypatch.setattr(study, "fetch_cached_session", refused)


@pytest.mark.parametrize("url", [None, "postgresql://u:p@db.example.com:5432/db"], ids=["unset", "remote"])
def test_the_study_script_exits_before_reading_a_database_that_is_not_local(monkeypatch, url):
    study = _study_script()
    _forbid_reads(monkeypatch, study)
    for name in ("DATABASE_URL", "POSTGRES_URL", "RENDER_POSTGRES_URL", "PGHOSTADDR", "PGSERVICE"):
        monkeypatch.delenv(name, raising=False)
    if url is not None:
        monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr("sys.argv", ["probe_alignment_study.py", "--summary"])
    with pytest.raises(SystemExit) as stopped:
        study.main()
    assert "local" in str(stopped.value.code)


def test_the_study_script_leaves_out_the_bench_sessions_unless_asked(monkeypatch):
    study = _study_script()
    listed = [{"session_id": "bench-long"}, {"session_id": "s-1"}, {"session_id": "bench-small"}, {"session_id": "s-2"}]
    monkeypatch.setattr(study, "admin_list_sessions", lambda limit: listed)
    parser_args = study.argparse.Namespace(session=None, limit=10, include_bench=False)
    assert study._session_ids(parser_args) == ["s-1", "s-2"]
    parser_args.include_bench = True
    assert study._session_ids(parser_args) == ["bench-long", "s-1", "bench-small", "s-2"]


def test_the_study_script_prints_times_never_text(capsys):
    study = _study_script()
    row = synthetic.cached_row()
    study.report({row["session_id"]: row})
    output = capsys.readouterr().out
    assert "verified" in output and "+18.9" in output and "±5.7" in output
    assert "link 1/1" in output and "wave 1/1" in output and "read_aloud 1/1" in output
    assert "00:22" in output and "04:49" in output, "the ear-check worksheet gives recording times"
    private = synthetic.PRIVATE_VALUES
    assert [value for value in private if value in output] == [] and "@" not in output


def test_the_study_summary_counts_statuses_only(capsys):
    study = _study_script()
    rows = {"a": synthetic.cached_row(), "b": {**synthetic.cached_row(), "transcript_payload": None}}
    study.report(rows, summary=True)
    output = capsys.readouterr().out
    assert "verified: 1" in output and "unavailable: 1" in output
    assert not re.search(r"^(a|b) ", output, re.MULTILINE) and synthetic.SESSION_ID not in output
