"""The v1 transcript parser, speech events and transcript health (`livestorm_app/record/`).

`record/transcript.py`, `record/clock.speech_events` and
`record/transcript_health.py` on synthetic Gladia payloads in three shapes:
one chunk, a long recording merged by today's `_merge_chunk_results`, and a
long recording merged before the merge fix (the shape older stored
transcripts have). No real transcript is used. Pure functions: no database,
Redis or network.
"""

from __future__ import annotations

import copy
import json
import math

import pandas as pd
import pytest

from livestorm_app.analytics.transcript import build_transcript_insights
from livestorm_app.gladia import transcriber
from livestorm_app.record import clock
from livestorm_app.record.transcript import (
    NO_WORDS,
    SENTENCE_COLUMNS,
    UTTERANCE_COLUMNS,
    WORD_COLUMNS,
    WORDS_FROM_TRANSCRIPTION,
    WORDS_FROM_UTTERANCES,
    parse_sentences,
    parse_transcript,
    parse_utterances,
    parse_words,
)
from livestorm_app.record.transcript_health import (
    CLUSTER_TOP,
    DEGRADED,
    HEALTH_VERSION,
    OK,
    POOR,
    UNAVAILABLE,
    build_transcript_health,
    union_seconds,
    unclear_clusters,
)
from test_gladia_merge import CHUNK_SECONDS, chunk_result, gladia_chunk, two_chunks, utterance

# ── Synthetic payloads ──────────────────────────────────────────────────────


def single_chunk(*utterances, duration=600.0, **result_extra):
    """One Gladia job, as today's pipeline stores it: merged (one chunk, so
    `stormiq_chunks` is written) and normalized."""
    chunk = gladia_chunk(list(utterances) or [utterance(0, 10.0, 5)], duration=duration, job_id="job-1")
    chunk["request_params"] = {"model": "solaria-1"}
    chunk["result"].update(result_extra)
    merged = transcriber._merge_chunk_results([chunk_result(chunk, 0.0)])
    return normalized(merged)


def normalized(gladia_payload):
    from pathlib import Path

    return transcriber._normalize_transcription(
        gladia_payload, source_video=Path("recording.mp4"), extracted_audio=None,
        requested_model=transcriber.DEFAULT_MODEL, actual_model=transcriber.DEFAULT_MODEL,
    )


def merged_after_fix():
    """A 155-minute recording merged by today's `_merge_chunk_results`."""
    return normalized(transcriber._merge_chunk_results(two_chunks()))


def merged_before_fix(chunks=None):
    """The same recording as `_merge_chunk_results` wrote it before the merge
    fix: each utterance's own start and end shifted, but not the words inside
    it; speakers numbered from 0 in every chunk; an empty
    `transcription.words` list; Gladia's ids renumbered; no chunk map."""
    chunks = two_chunks() if chunks is None else chunks
    merged = copy.deepcopy(chunks[0]["gladia_result"])
    utterances, words = [], []
    for chunk in chunks:
        offset = chunk["offset_seconds"]
        transcription = chunk["gladia_result"]["result"]["transcription"]
        for item in transcription["utterances"]:
            utterances.append({**item, "start": item["start"] + offset, "end": item["end"] + offset})
        words.extend({**w, "start": w["start"] + offset, "end": w["end"] + offset} for w in transcription.get("words", []))
    for index, item in enumerate(utterances, start=1):
        item["id"] = index
    merged["result"]["transcription"]["utterances"] = utterances
    merged["result"]["transcription"]["words"] = words
    merged["result"]["metadata"]["audio_duration"] = CHUNK_SECONDS + 1200.0
    return normalized(merged)


def words_by_uid(parsed):
    return {uid: group for uid, group in parsed.words.groupby("uid", sort=False)}


# ── The parser ──────────────────────────────────────────────────────────────


def test_a_single_chunk_parses_utterances_and_their_words():
    payload = single_chunk(utterance(0, 10.0, 5), utterance(1, 20.0, 3, confidence=0.5))
    parsed = parse_transcript(payload)

    assert list(parsed.utterances.columns) == list(UTTERANCE_COLUMNS)
    assert list(parsed.words.columns) == list(WORD_COLUMNS)
    assert parsed.utterances["uid"].tolist() == ["U0001", "U0002"]
    assert parsed.utterances["speaker_key"].tolist() == ["0", "1"]
    assert parsed.utterances["n_words"].tolist() == [5, 3]
    assert parsed.utterances["conf_mean"].tolist() == [0.9, 0.5]
    assert parsed.words["uid"].tolist() == ["U0001"] * 5 + ["U0002"] * 3
    assert parsed.words["word"].tolist()[:2] == ["w0", "w1"]
    assert (parsed.words_source, parsed.rebased_utterances, parsed.chunks) == (WORDS_FROM_UTTERANCES, 0, 1)
    assert (parsed.audio_s, parsed.model, parsed.languages) == (600.0, "solaria-1", ("en",))


def test_utterance_ids_follow_start_then_end_then_payload_order():
    late, short, long_, same = utterance(0, 50.0, 2), utterance(1, 10.0, 2), utterance(2, 10.0, 4), utterance(3, 10.0, 2)
    payload = single_chunk(late, short, long_, same)
    parsed = parse_transcript(payload)
    # short and same tie on start and end: payload order decides.
    assert parsed.utterances["speaker_key"].tolist() == ["1", "3", "2", "0"]
    assert parsed.utterances["order"].tolist() == [1, 3, 2, 0]
    assert parsed.utterances["uid"].tolist() == ["U0001", "U0002", "U0003", "U0004"]


def test_utterance_ids_are_stable_across_parses_and_gladia_ids_are_ignored():
    payload = merged_after_fix()
    before = copy.deepcopy(payload)
    first = parse_transcript(payload)
    renumbered = copy.deepcopy(payload)
    for item in renumbered["result"]["transcription"]["utterances"]:
        item["id"] = 1000 - item["id"]
    second = parse_transcript(renumbered)
    pd.testing.assert_frame_equal(first.utterances, second.utterances)
    pd.testing.assert_frame_equal(first.words, second.words)
    assert payload == before, "parsing never changes the payload"


def test_utterances_without_a_start_come_last_in_payload_order():
    payload = single_chunk(utterance(0, 30.0, 2), utterance(1, 10.0, 2))
    payload["result"]["transcription"]["utterances"].insert(0, {"speaker": 2, "text": "no time", "words": []})
    parsed = parse_transcript(payload)
    assert parsed.utterances["speaker_key"].tolist() == ["1", "0", "2"]
    assert math.isnan(parsed.utterances["r_start"].iloc[-1])


def test_a_merged_long_recording_is_read_as_stored():
    parsed = parse_transcript(merged_after_fix())
    assert (parsed.chunks, parsed.rebased_utterances, parsed.words_source) == (2, 0, WORDS_FROM_UTTERANCES)
    assert parsed.utterances["speaker_key"].tolist() == ["0", "1", "0", "2", "3", "2"]
    for uid, words in words_by_uid(parsed).items():
        row = parsed.utterances.set_index("uid").loc[uid]
        assert row["r_start"] <= words["r_start"].min() and words["r_end"].max() <= row["r_end"]
    assert parsed.words["r_start"].max() > CHUNK_SECONDS


def test_an_old_chunked_transcript_falls_back_to_the_utterances_words_and_rebases_them():
    """Before the merge fix, the empty `transcription.words` list hid the
    utterances' words, and chunk 2's words kept chunk 2's own clock. The v1
    parser reads the utterances' words and moves chunk 2's onto their
    utterances: the same words, at the same times, as a payload merged today."""
    old = merged_before_fix()
    assert old["result"]["transcription"]["words"] == [] and old["words"] == []
    assert "stormiq_chunks" not in old["result"]
    parsed = parse_transcript(old)
    fixed = parse_transcript(merged_after_fix())

    assert (parsed.words_source, parsed.rebased_utterances, parsed.chunks) == (WORDS_FROM_UTTERANCES, 3, 2)
    pd.testing.assert_series_equal(parsed.words["r_start"], fixed.words["r_start"])
    pd.testing.assert_series_equal(parsed.words["uid"], fixed.words["uid"])
    assert parsed.utterances["n_words"].tolist() == fixed.utterances["n_words"].tolist()


def test_an_old_chunked_transcript_with_gladias_word_list_reads_that_list():
    """The old merge shifted `transcription.words` correctly, so when Gladia
    sent that list it is read as stored, and nothing is re-based."""
    old = merged_before_fix()
    fixed = merged_after_fix()
    old["result"]["transcription"]["words"] = [
        {key: word[key] for key in ("word", "start", "end", "confidence")}
        for item in fixed["result"]["transcription"]["utterances"]
        for word in item["words"]
    ]
    parsed = parse_transcript(old)
    assert (parsed.words_source, parsed.rebased_utterances, parsed.chunks) == (WORDS_FROM_TRANSCRIPTION, 0, 2)
    pd.testing.assert_series_equal(parsed.words["uid"], parse_transcript(fixed).words["uid"])


def test_classic_still_reads_an_old_chunked_transcript_as_before():
    """Nothing classic changes: classic reads the empty list and gets no words."""
    insights = build_transcript_insights(merged_before_fix())
    assert insights["words_df"].empty


def test_rebasing_is_only_for_old_chunked_transcripts():
    """A payload with a chunk map is read as stored, even with a word
    outside its utterance, and even when it also has an empty
    `transcription.words` list (today's single-chunk merge keeps Gladia's
    result as it is, so that list can be there)."""
    payload = merged_after_fix()
    payload["result"]["transcription"]["utterances"][3]["words"][0]["start"] -= 500
    parsed = parse_transcript(payload)
    assert parsed.rebased_utterances == 0
    assert parsed.words["r_start"].min() < CHUNK_SECONDS - 400

    single = single_chunk(utterance(0, 10.0, 5))
    assert "stormiq_chunks" in single["result"]
    single["result"]["transcription"]["words"] = []
    single["result"]["transcription"]["utterances"][0]["words"][0]["start"] = 5.0  # 5 s before its utterance
    parsed_single = parse_transcript(single)
    assert (parsed_single.rebased_utterances, parsed_single.chunks) == (0, 1)
    assert parsed_single.words["r_start"].tolist()[:2] == [5.0, 10.4], "times as stored"


def test_an_old_transcript_without_the_words_list_is_one_chunk_and_is_not_rebased():
    """Before the merge fix, a single chunk was saved as Gladia sent it: no
    chunk map and no `transcription.words` list. It is read as stored."""
    payload = single_chunk(utterance(0, 10.0, 3))
    del payload["result"]["stormiq_chunks"]
    assert "words" not in payload["result"]["transcription"]
    payload["result"]["transcription"]["utterances"][0]["words"][0]["start"] = 5.0  # 5 s before its utterance
    parsed = parse_transcript(payload)
    assert (parsed.rebased_utterances, parsed.chunks) == (0, 1)
    assert parsed.words["r_start"].iloc[0] == 5.0


def test_a_word_up_to_1_second_outside_its_utterance_is_not_moved():
    """In an old chunked transcript, only words more than 1 s outside
    their utterance are re-based."""
    old = merged_before_fix()
    first = old["result"]["transcription"]["utterances"][0]
    first["words"][0]["start"] = first["start"] - 0.5
    parsed = parse_transcript(old)
    assert parsed.rebased_utterances == 3, "only chunk 2's utterances, as before"
    assert parsed.words["r_start"].iloc[0] == first["start"] - 0.5


def test_three_old_chunks_count_as_three():
    chunks = two_chunks() + [
        chunk_result(gladia_chunk([utterance(0, 3.0, 4)], duration=600.0, job_id="job-3"), 2 * CHUNK_SECONDS)
    ]
    parsed = parse_transcript(merged_before_fix(chunks))
    assert (parsed.chunks, parsed.rebased_utterances) == (3, 4)


def test_a_non_empty_transcription_words_list_is_read_and_matched_to_utterances():
    payload = single_chunk(utterance(0, 10.0, 3), utterance(1, 20.0, 2))
    payload["result"]["transcription"]["words"] = [
        {"word": " a", "start": 10.0, "end": 10.3, "confidence": 0.8},
        {"word": " b", "start": 20.1, "end": 20.4, "confidence": 0.7, "speaker": 5},
        {"word": " c", "start": 15.0, "end": 15.2, "confidence": 0.6},  # between the two utterances
    ]
    parsed = parse_transcript(payload)
    assert parsed.words_source == WORDS_FROM_TRANSCRIPTION
    assert parsed.words["uid"].tolist() == ["U0001", "U0002", None]
    assert parsed.words["speaker_key"].tolist() == ["0", "5", None]
    assert parsed.utterances["n_words"].tolist() == [1, 1]


def test_a_word_inside_a_long_utterance_that_a_shorter_one_overlaps_finds_it():
    """Each word goes to the latest-starting utterance that starts at or
    before its middle and reaches it (within 1 s)."""
    payload = {"result": {"transcription": {
        "utterances": [
            {"start": 0.0, "end": 100.0, "speaker": 0, "words": []},  # U0001
            {"start": 50.0, "end": 55.0, "speaker": 1, "words": []},  # U0002, inside U0001
        ],
        "words": [
            {"word": " a", "start": 52.0, "end": 52.2},  # in both: the later start
            {"word": " b", "start": 55.5, "end": 55.7},  # 0.6 s past U0002, inside U0001: still U0002
            {"word": " c", "start": 70.0, "end": 70.3},  # past U0002, inside U0001
            {"word": " d", "start": 150.0, "end": 150.3},  # in none
        ],
    }}}
    parsed = parse_transcript(payload)
    assert parsed.words["uid"].tolist() == ["U0002", "U0002", "U0001", None]
    assert parsed.words["speaker_key"].tolist() == ["1", "1", "0", None]


def test_the_normalized_top_level_words_are_never_read():
    payload = single_chunk(utterance(0, 10.0, 2))
    payload["words"] = [{"word": "ghost", "start": 1.0, "end": 1.2, "confidence": 0.1}]
    assert parse_words(payload)["word"].tolist() == ["w0", "w1"]


def test_unreadable_values_are_missing_not_zero():
    payload = single_chunk(utterance(0, 10.0, 2))
    item = payload["result"]["transcription"]["utterances"][0]
    item["speaker"] = True
    item["words"][0]["confidence"] = 1.5
    item["words"][1]["confidence"] = "0.9"
    parsed = parse_transcript(payload)
    assert parsed.utterances["speaker_key"].tolist() == [None]
    assert parsed.words["confidence"].isna().all()
    assert parsed.utterances["n_words"].tolist() == [2] and math.isnan(parsed.utterances["conf_mean"].iloc[0])


@pytest.mark.parametrize("payload", [None, {}, {"text": "x"}, {"result": {"transcription": "x"}}, {"transcript": {"segments": []}}])
def test_a_payload_without_a_gladia_transcription_does_not_parse(payload):
    assert parse_transcript(payload) is None
    assert list(parse_utterances(payload).columns) == list(UTTERANCE_COLUMNS) and parse_utterances(payload).empty
    assert list(parse_words(payload).columns) == list(WORD_COLUMNS) and parse_words(payload).empty


def test_a_transcription_without_utterances_parses_to_empty_frames():
    parsed = parse_transcript({"result": {"transcription": {"utterances": []}, "stormiq_chunks": [{"index": 1}]}})
    assert parsed.utterances.empty and parsed.words.empty
    assert (parsed.words_source, parsed.chunks, parsed.audio_s) == (NO_WORDS, 1, None)


def test_sentences_in_every_known_shape():
    wrapped = single_chunk(utterance(0, 10.0, 2), utterance(1, 4.0, 2))
    frame = parse_sentences(wrapped)
    assert list(frame.columns) == list(SENTENCE_COLUMNS)
    assert frame["r_start"].tolist() == [4.0, 10.0] and frame["speaker_key"].tolist() == ["1", "0"]

    plain = {"result": {"transcription": {}, "sentences": ["  First one. ", "", {"text": "Second", "start": 2.0, "end": 3.0}]}}
    frame = parse_sentences(plain)
    assert frame["text"].tolist() == ["Second", "First one."], "timed first; plain strings have no time"
    assert math.isnan(frame["r_start"].iloc[1])

    direct = {"result": {"transcription": {"sentences": [{"sentence": "Direct", "start": 1.0}, {"sentence": "  "}]},
                         "sentences": {"success": True, "results": [{"sentence": "Ignored", "start": 0.0}]}}}
    assert parse_sentences(direct)["text"].tolist() == ["Direct"]
    empty_direct = {"result": {"transcription": {"sentences": []},
                               "sentences": {"success": True, "results": [{"sentence": "Fallback", "start": 0.0}]}}}
    assert parse_sentences(empty_direct)["text"].tolist() == ["Fallback"], "an empty list never hides the block"
    assert parse_sentences({"result": {"transcription": {}}}).empty
    assert parse_sentences(None).empty


# ── Speech events ───────────────────────────────────────────────────────────


def test_speech_events_put_utterances_on_the_session_clock():
    parsed = parse_transcript(single_chunk(utterance(0, 10.0, 5), utterance(1, 70.0, 3)))
    events = clock.speech_events(parsed.utterances, 40)
    assert list(events.columns) == list(clock.SPEECH_EVENT_COLUMNS)
    assert events["eid"].tolist() == ["U0001", "U0002"]
    assert events["t_start"].tolist() == [50.0, 110.0]
    assert (events["t_end"] - events["r_end"]).tolist() == [40.0, 40.0]
    assert events["n_words"].tolist() == [5, 3]


def test_speech_without_an_offset_is_not_placed():
    parsed = parse_transcript(single_chunk(utterance(0, 10.0, 5)))
    events = clock.speech_events(parsed.utterances, None)
    assert events["t_start"].isna().all() and events["t_end"].isna().all()
    base = clock.clock_base({"data": {"attributes": {"started_at": 1_800_000_000, "ended_at": 1_800_000_600}}})
    series = clock.minute_bins(None, events, base=base)
    assert series.minutes["speaking_s"].sum() == 0


def test_speech_events_feed_the_speaking_lanes():
    parsed = parse_transcript(single_chunk(utterance(0, 50.0, 30)))  # r = 50 → 61.9
    events = clock.speech_events(parsed.utterances, 0)
    base = clock.clock_base({"data": {"attributes": {"started_at": 1_800_000_000, "ended_at": 1_800_000_600}}})
    minutes = clock.minute_bins(None, events, base=base).minutes
    assert minutes.loc[0, "speaking_s"] == 10.0 and minutes.loc[1, "speaking_s"] == pytest.approx(1.9)
    assert minutes["words"].sum() == pytest.approx(30)


def test_no_utterances_give_no_speech_events():
    events = clock.speech_events(pd.DataFrame(columns=list(UTTERANCE_COLUMNS)), 0)
    assert events.empty and list(events.columns) == list(clock.SPEECH_EVENT_COLUMNS)
    assert clock.speech_events(None, 0).empty


# ── Health: words ───────────────────────────────────────────────────────────


def health(payload, **options):
    return build_transcript_health(parse_transcript(payload), **options)


def test_confidence_is_counted_per_word_not_per_utterance():
    long_good = utterance(0, 10.0, 8, confidence=0.9)
    short_bad = utterance(1, 30.0, 2, confidence=0.5)
    short_bad["confidence"] = 0.99  # the utterance's own confidence is never used
    words = health(single_chunk(long_good, short_bad))["words"]
    assert words["meanConfidence"] == pytest.approx((8 * 0.9 + 2 * 0.5) / 10)
    assert words["lowConfidenceShare"] == 0.2
    assert (words["count"], words["withConfidence"]) == (10, 10)


def test_no_words_give_empty_word_fields_not_zero():
    payload = single_chunk(utterance(0, 10.0, 3))
    for item in payload["result"]["transcription"]["utterances"]:
        item["words"] = []
    found = health(payload)
    assert found["words"] == {
        "count": 0, "withConfidence": 0, "meanConfidence": None, "lowConfidenceShare": None,
        "source": NO_WORDS, "rebasedUtterances": 0,
    }
    assert found["unclearClusters"] == []
    assert "no_word_data" in found["reasons"]


def test_an_old_chunked_transcript_has_its_words_and_says_it_was_rebased():
    words = health(merged_before_fix())["words"]
    assert words["count"] == 30 and words["rebasedUtterances"] == 3


# ── Health: coverage ────────────────────────────────────────────────────────


def test_coverage_is_the_union_of_utterance_spans():
    """Two speakers talking over each other count once."""
    first = utterance(0, 100.0, 50)  # 100 → 119.9
    overlapping = utterance(1, 110.0, 50)  # 110 → 129.9
    coverage = health(single_chunk(first, overlapping, duration=200.0), live_s=400.0)["coverage"]
    assert coverage["speechS"] == pytest.approx(29.9)
    assert coverage["shareOfAudio"] == pytest.approx(29.9 / 200, abs=1e-4)
    assert coverage["shareOfLive"] == pytest.approx(29.9 / 400, abs=1e-4)
    assert coverage["firstSpeechR"] == 100 and coverage["tailS"] == pytest.approx(70.1)
    assert union_seconds([(0.0, 10.0), (5.0, 8.0), (20.0, 25.0)]) == 15.0


def test_speech_past_the_recording_never_passes_100_percent():
    coverage = health(single_chunk(utterance(0, 0.0, 300), duration=60.0))["coverage"]
    assert coverage["shareOfAudio"] == 1.0 and coverage["tailS"] == 0


def test_coverage_without_a_recording_length_or_live_length():
    payload = single_chunk(utterance(0, 10.0, 3))
    del payload["result"]["metadata"]["audio_duration"]
    del payload["file"]["audio_duration"]
    payload["duration_seconds"] = 300.0
    assert health(payload)["coverage"]["audioS"] == 300, "the transcript's own duration comes last"
    payload["duration_seconds"] = None
    coverage = health(payload)["coverage"]
    assert (coverage["audioS"], coverage["shareOfAudio"], coverage["liveS"], coverage["shareOfLive"], coverage["tailS"]) == (
        None, None, None, None, None,
    )


# ── Health: speakers ────────────────────────────────────────────────────────


def talk(speaker, start, seconds):
    """`seconds` of speech by `speaker` from `start` (words of 0.3 s, 0.1 s apart)."""
    return utterance(speaker, start, max(1, int(seconds / 0.4)))


def test_tiny_phantom_speakers_are_not_substantial():
    payload = single_chunk(
        talk(0, 0.0, 1200), talk(1, 1300.0, 600),
        talk(2, 2000.0, 5),  # about 5 s: under 30 s and under 2% of the speech
        talk(3, 2100.0, 35),  # about 35 s: over 30 s, but under 2% of the speech
        duration=2400.0,
    )
    speakers = health(payload)["speakers"]
    assert (speakers["detected"], speakers["substantial"]) == (4, 2)


def test_a_speaker_under_30_seconds_is_not_substantial_whatever_their_share():
    payload = single_chunk(
        talk(0, 0.0, 280),
        talk(1, 300.0, 20),  # about 20 s: over 2% of the speech (about 7%), but under 30 s
        duration=330.0,
    )
    speakers = health(payload)["speakers"]
    assert (speakers["detected"], speakers["substantial"]) == (2, 1)


def test_split_risk_when_substantial_speakers_pass_people_on_stage_plus_one():
    payload = single_chunk(*(talk(index, index * 300.0, 250) for index in range(4)), duration=1200.0)
    assert health(payload, people_on_stage=2)["speakers"]["splitRisk"] is True
    assert health(payload, people_on_stage=3)["speakers"]["splitRisk"] is False
    unknown = health(payload)["speakers"]
    assert (unknown["expected"], unknown["expectedSource"], unknown["splitRisk"]) == (None, None, False)


def test_more_than_one_chunk_is_a_split_risk():
    found = health(merged_after_fix())
    assert found["speakers"]["chunks"] == 2 and found["speakers"]["splitRisk"] is True
    assert (found["state"], found["reasons"]) == (DEGRADED, ["little_speech", "split_risk"])


def test_a_split_risk_alone_is_degraded():
    payload = single_chunk(*(talk(index, index * 300.0, 250) for index in range(4)), duration=1200.0)
    found = health(payload, people_on_stage=2)
    assert found["coverage"]["shareOfAudio"] >= 0.6 and found["words"]["meanConfidence"] >= 0.85
    assert (found["state"], found["reasons"]) == (DEGRADED, ["split_risk"])


def test_expected_speakers_come_from_the_transcript_before_the_roster():
    payload = single_chunk(talk(0, 0.0, 300))
    payload["stormiq"] = {"people_on_stage": 3, "max_speakers": 5}
    speakers = health(payload, people_on_stage=1)["speakers"]
    assert (speakers["expected"], speakers["expectedSource"], speakers["cap"]) == (3, "transcript", 5)
    payload["stormiq"]["people_on_stage"] = None
    speakers = health(payload, people_on_stage=1)["speakers"]
    assert (speakers["expected"], speakers["expectedSource"]) == (1, "roster")


def test_labelled_speakers_are_counted_and_confirm_speaker_shares():
    payload = single_chunk(*(talk(index, index * 300.0, 250) for index in range(4)), duration=1200.0)
    unconfirmed = health(payload, people_on_stage=1, speaker_names={"0": "Pat", "1": " ", "9": "Nobody"})
    assert (unconfirmed["speakers"]["labelled"], unconfirmed["speakers"]["substantialLabelled"]) == (1, 1)
    assert unconfirmed["gates"]["speakerShares"] is False
    names = {str(index): f"Speaker {index}" for index in range(4)}
    confirmed = health(payload, people_on_stage=1, speaker_names=names)
    assert confirmed["speakers"]["splitRisk"] is True and confirmed["gates"]["speakerShares"] is True
    assert "Speaker" not in json.dumps(confirmed), "labels are counted, never sent"


def test_speaker_shares_need_a_substantial_speaker():
    """No speaker at all (diarization found none), or only short ones: there
    is nothing to share, with or without a split risk."""
    unlabelled = single_chunk(talk(0, 0.0, 90), duration=95.0)
    for item in unlabelled["result"]["transcription"]["utterances"]:
        del item["speaker"]
    found = health(unlabelled)
    assert (found["speakers"]["detected"], found["speakers"]["substantial"]) == (0, 0)
    assert found["gates"] == {"pace": True, "speakerShares": False}
    assert (found["state"], found["reasons"]) == (DEGRADED, ["no_speakers"])

    short = single_chunk(talk(0, 0.0, 20), talk(1, 21.0, 20), duration=45.0)
    found = health(short)
    assert (found["speakers"]["detected"], found["speakers"]["substantial"], found["speakers"]["splitRisk"]) == (2, 0, False)
    assert found["gates"]["speakerShares"] is False
    assert "no_speakers" not in found["reasons"], "speakers were found, only short ones"

    split = single_chunk(talk(0, 0.0, 20), talk(1, 21.0, 20), duration=45.0)
    split["result"]["stormiq_chunks"].append({"index": 2})
    found = health(split, speaker_names={})
    assert found["speakers"]["splitRisk"] is True and found["speakers"]["substantialLabelled"] == 0
    assert found["gates"]["speakerShares"] is False


# ── Health: language ────────────────────────────────────────────────────────


def test_language_against_the_events_language():
    english = single_chunk(talk(0, 0.0, 600), duration=700.0)
    assert health(english, expected_language="en-GB")["language"]["match"] is True
    mismatch = health(english, expected_language="fr")
    assert mismatch["language"] == {"detected": "en", "expected": "fr", "match": False, "shares": {"en": 1.0}}
    assert mismatch["state"] == DEGRADED and mismatch["reasons"] == ["language_mismatch"]
    assert health(english)["language"]["match"] is None


def test_language_shares_follow_speech_seconds():
    french = talk(0, 0.0, 300)
    french["language"] = "fr"
    payload = single_chunk(french, talk(1, 400.0, 100), duration=500.0)
    assert health(payload)["language"]["shares"] == {"fr": pytest.approx(0.75, abs=1e-3), "en": pytest.approx(0.25, abs=1e-3)}


def test_language_shares_count_utterances_when_none_has_a_length():
    payload = {"result": {"transcription": {"utterances": [
        {"start": 0.0, "speaker": 0, "language": "fr"},
        {"start": 10.0, "speaker": 0, "language": "fr-FR"},
        {"start": 20.0, "speaker": 1, "language": "en"},
    ]}}}
    assert health(payload)["language"]["shares"] == {"fr": pytest.approx(0.6667), "en": pytest.approx(0.3333)}


def test_the_detected_language_falls_back_to_the_metadata():
    payload = single_chunk(talk(0, 0.0, 300))
    del payload["result"]["transcription"]["languages"]
    payload["result"]["metadata"]["language"] = "FR"
    assert parse_transcript(payload).languages == ("fr",)
    assert health(payload, expected_language="fr-FR")["language"]["match"] is True


# ── Health: unclear clusters ────────────────────────────────────────────────


def low_words(*starts, uid="U0001"):
    return pd.DataFrame(
        {"uid": [uid] * len(starts), "r_start": list(starts), "r_end": [s + 0.3 for s in starts],
         "confidence": [0.3] * len(starts), "speaker_key": ["0"] * len(starts), "word": ["w"] * len(starts)}
    )


def test_three_unclear_words_within_30_seconds_make_a_cluster():
    assert unclear_clusters(low_words(10.0, 20.0, 39.0)) == [{"t0R": 10, "t1R": 39.3, "nLow": 3, "u": ["U0001"]}]
    assert unclear_clusters(low_words(10.0, 20.0, 40.0)) == [], "the third word is 30 s after the first"
    assert unclear_clusters(low_words(10.0, 20.0)) == []


def test_overlapping_windows_merge_and_clear_words_do_not_count():
    words = pd.concat([low_words(0.0, 10.0, 20.0, 35.0, 45.0, uid="U0002"), low_words(100.0, uid="U0009")], ignore_index=True)
    clear = low_words(5.0, 6.0, 7.0)
    clear["confidence"] = 0.9
    found = unclear_clusters(pd.concat([words, clear], ignore_index=True))
    assert found == [{"t0R": 0, "t1R": 45.3, "nLow": 5, "u": ["U0002"]}]


def test_windows_that_share_only_one_word_merge():
    """The window from 0 s ends on the word at 20 s, and the window from
    20 s starts on it."""
    assert unclear_clusters(low_words(0.0, 10.0, 20.0, 45.0, 49.0)) == [{"t0R": 0, "t1R": 49.3, "nLow": 5, "u": ["U0001"]}]


def test_only_the_largest_clusters_are_kept_in_time_order():
    starts = []
    for index in range(CLUSTER_TOP + 2):
        size = 3 if index < 2 else 4  # the first two are the smallest
        starts += [index * 100.0 + k for k in range(size)]
    found = unclear_clusters(low_words(*starts))
    assert len(found) == CLUSTER_TOP
    assert [cluster["t0R"] for cluster in found] == [index * 100 for index in range(2, CLUSTER_TOP + 2)]


def test_a_transcript_with_an_unclear_stretch_lists_its_utterances():
    payload = single_chunk(utterance(0, 0.0, 20), utterance(1, 10.0, 4, confidence=0.3), utterance(0, 30.0, 10), duration=60.0)
    clusters = health(payload)["unclearClusters"]
    assert clusters == [{"t0R": 10, "t1R": 11.5, "nLow": 4, "u": ["U0002"]}]


# ── Health: state and gates ─────────────────────────────────────────────────


def test_a_clear_full_transcript_is_ok_and_opens_both_gates():
    found = health(single_chunk(talk(0, 0.0, 500), talk(1, 520.0, 60), duration=600.0), people_on_stage=2, expected_language="en")
    assert (found["version"], found["state"], found["reasons"], found["model"]) == (HEALTH_VERSION, OK, [], "solaria-1")
    assert found["gates"] == {"pace": True, "speakerShares": True}


def test_low_confidence_is_poor():
    found = health(single_chunk(utterance(0, 0.0, 1400, confidence=0.5), duration=600.0))
    assert found["state"] == POOR and found["reasons"] == ["low_confidence", "unclear_words"]


def test_some_unclear_words_are_degraded():
    """About 15% of the words below 0.6, with a mean that stays above 0.85."""
    payload = single_chunk(utterance(0, 0.0, 1020, confidence=0.99), utterance(1, 410.0, 180, confidence=0.5), duration=500.0)
    found = health(payload)
    assert found["words"]["lowConfidenceShare"] == 0.15 and found["words"]["meanConfidence"] >= 0.85
    assert (found["state"], found["reasons"]) == (DEGRADED, ["unclear_words"])


def test_mostly_silence_is_degraded_and_closes_the_pace_gate():
    found = health(single_chunk(talk(0, 0.0, 100), duration=600.0))
    assert found["state"] == DEGRADED and found["reasons"] == ["little_speech"]
    assert found["gates"]["pace"] is False


def test_speech_without_any_speaker_is_degraded_not_ok():
    """A clear, full transcript in which diarization found no speaker: the
    state can't read ok without saying so."""
    payload = single_chunk(talk(0, 0.0, 500), talk(1, 520.0, 60), duration=600.0)
    for item in payload["result"]["transcription"]["utterances"]:
        del item["speaker"]
    found = health(payload, people_on_stage=2, expected_language="en")
    assert found["speakers"]["detected"] == 0 and found["words"]["meanConfidence"] >= 0.85
    assert (found["state"], found["reasons"]) == (DEGRADED, ["no_speakers"])
    assert found["gates"] == {"pace": True, "speakerShares": False}

    payload["result"]["stormiq_chunks"].append({"index": 2})
    assert health(payload)["reasons"] == ["no_speakers", "split_risk"], "listed in the order they are checked"

    poor = single_chunk(utterance(0, 0.0, 1400, confidence=0.5), duration=600.0)
    del poor["result"]["transcription"]["utterances"][0]["speaker"]
    found = health(poor)
    assert (found["state"], found["reasons"]) == (POOR, ["low_confidence", "unclear_words", "no_speakers"])


def test_one_speaker_found_is_enough_to_not_say_no_speakers():
    payload = single_chunk(talk(0, 0.0, 500), talk(1, 520.0, 60), duration=600.0)
    del payload["result"]["transcription"]["utterances"][1]["speaker"]
    found = health(payload, people_on_stage=2, expected_language="en")
    assert found["speakers"]["detected"] == 1
    assert (found["state"], found["reasons"]) == (OK, [])


def test_a_transcript_without_speech_is_poor():
    payload = single_chunk()
    payload["result"]["transcription"]["utterances"] = []
    found = health(payload)
    assert found["state"] == POOR and found["reasons"] == ["no_speech"]
    assert found["gates"] == {"pace": False, "speakerShares": False}


def test_no_transcript_is_unavailable():
    found = build_transcript_health(None, live_s=600.0, people_on_stage=2)
    assert found == {
        "version": HEALTH_VERSION, "state": UNAVAILABLE, "reasons": ["no_transcript"], "model": None,
        "words": None, "coverage": None, "speakers": None, "language": None, "unclearClusters": [],
        "gates": {"pace": False, "speakerShares": False},
    }


def test_health_carries_no_word_or_text_and_is_strict_json():
    payload = single_chunk(utterance(0, 0.0, 30, confidence=0.4), duration=60.0)
    text = json.dumps(health(payload, speaker_names={"0": "Pat Example"}), allow_nan=False)
    assert "w0" not in text and "Pat" not in text and '"text"' not in text


def test_record_modules_stay_pure():
    from pathlib import Path

    import livestorm_app.record.transcript as transcript_module
    import livestorm_app.record.transcript_health as health_module

    for module in (transcript_module, health_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for name in ("livestorm_app.db", "requests", "fastapi", "psycopg", "redis", "livestorm_app.gladia", "livestorm_app.analytics"):
            assert f"import {name}" not in source and f"from {name}" not in source
