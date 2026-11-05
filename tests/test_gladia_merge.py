"""The merge of a long recording's Gladia chunks (`gladia/transcriber._merge_chunk_results`).

A recording longer than 135 minutes is transcribed in chunks, and each chunk's
result starts at 0 s with speakers numbered from 0. These tests build synthetic
chunk results in the shape Gladia returns (words inside the utterances, no
`transcription.words`, sentences and named entities under `result`) and check
that the merged transcript is on the whole recording's clock. No network.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from livestorm_app.analytics.transcript import build_transcript_insights
from livestorm_app.gladia import transcriber

CHUNK_SECONDS = 135 * 60.0
WORD_SECONDS = 0.3
WORD_GAP_SECONDS = 0.1
HESITATION_SECONDS = 1.5


def utterance(speaker, start, n_words, *, confidence=0.9, hesitation_after=None):
    """One Gladia utterance with its words, on its chunk's own clock."""
    words = []
    t = float(start)
    for index in range(n_words):
        words.append({"word": f" w{index}", "start": round(t, 3), "end": round(t + WORD_SECONDS, 3), "confidence": confidence})
        t += WORD_SECONDS + (HESITATION_SECONDS if index == hesitation_after else WORD_GAP_SECONDS)
    text = "".join(word["word"] for word in words).strip()
    return {
        "start": words[0]["start"], "end": words[-1]["end"], "speaker": speaker, "channel": 0,
        "confidence": confidence, "language": "en", "text": text, "words": words,
    }


def gladia_chunk(utterances, *, duration, job_id, language="en", billing_time=None, transcription_time=None):
    """A finished Gladia job for one chunk: one sentence and one entity per utterance."""
    sentences = [
        {
            "sentence": u["text"], "start": u["start"], "end": u["end"], "speaker": u["speaker"], "channel": 0,
            "confidence": u["confidence"], "language": language, "words": copy.deepcopy(u["words"]),
        }
        for u in utterances
    ]
    entities = [
        {"entity_type": "ORGANIZATION", "text": "Acme", "start": u["words"][0]["start"], "end": u["words"][0]["end"]}
        for u in utterances
    ]
    metadata = {"audio_duration": duration, "number_of_distinct_channels": 1}
    if billing_time is not None:
        metadata["billing_time"] = billing_time
    if transcription_time is not None:
        metadata["transcription_time"] = transcription_time
    return {
        "id": job_id, "status": "done", "kind": "pre-recorded",
        "file": {"audio_duration": duration},
        "result": {
            "metadata": metadata,
            "transcription": {
                "languages": [language],
                "full_transcript": " ".join(u["text"] for u in utterances),
                "utterances": utterances,
            },
            "sentences": {"success": True, "is_empty": not sentences, "results": sentences},
            "named_entity_recognition": {"success": True, "is_empty": not entities, "results": entities},
        },
    }


def chunk_result(payload, offset_seconds):
    """What `transcribe_video` collects for each chunk before merging."""
    return {
        "gladia_result": payload,
        "offset_seconds": offset_seconds,
        "upload_payload": {"audio_url": f"https://gladia.example/{payload['id']}"},
        "gladia_request": {"audio_url": f"https://gladia.example/{payload['id']}"},
        "started_job": {"id": payload["id"]},
    }


def two_chunks():
    """A 155-minute recording: chunk 2 starts at 135 min and numbers its speakers from 0 again."""
    first = gladia_chunk(
        [utterance(0, 10.0, 6), utterance(1, 40.0, 5), utterance(0, 8000.0, 4)],
        duration=CHUNK_SECONDS, job_id="job-1", language="en", billing_time=8100, transcription_time=300,
    )
    second = gladia_chunk(
        [utterance(0, 5.0, 6, confidence=0.8, hesitation_after=2), utterance(1, 60.0, 5, confidence=0.8), utterance(0, 600.0, 4, confidence=0.8)],
        duration=1200.0, job_id="job-2", language="fr", billing_time=1200, transcription_time=60,
    )
    return [chunk_result(first, 0.0), chunk_result(second, CHUNK_SECONDS)]


def merged_two_chunks():
    return transcriber._merge_chunk_results(two_chunks())


# ── The merge ──────────────────────────────────────────────────────────────


def test_words_inside_later_chunks_are_shifted_with_their_utterance():
    utterances = merged_two_chunks()["result"]["transcription"]["utterances"]

    assert [u["start"] for u in utterances] == [10.0, 40.0, 8000.0, CHUNK_SECONDS + 5.0, CHUNK_SECONDS + 60.0, CHUNK_SECONDS + 600.0]
    for u in utterances:
        assert u["start"] == u["words"][0]["start"]
        assert u["end"] == u["words"][-1]["end"]
        assert all(u["start"] <= word["start"] <= word["end"] <= u["end"] for word in u["words"])


def test_no_transcription_words_list_hides_the_utterances_words():
    transcription = merged_two_chunks()["result"]["transcription"]

    assert "words" not in transcription


def test_sentences_of_every_chunk_are_kept_and_shifted():
    sentences_block = merged_two_chunks()["result"]["sentences"]
    sentences = sentences_block["results"]

    assert sentences_block["success"] is True and sentences_block["is_empty"] is False
    assert [s["start"] for s in sentences] == [10.0, 40.0, 8000.0, CHUNK_SECONDS + 5.0, CHUNK_SECONDS + 60.0, CHUNK_SECONDS + 600.0]
    assert sentences[3]["words"][0]["start"] == CHUNK_SECONDS + 5.0
    assert all(s["start"] <= word["start"] <= s["end"] for s in sentences for word in s["words"])


def test_named_entities_of_every_chunk_are_kept_and_shifted():
    entities = merged_two_chunks()["result"]["named_entity_recognition"]["results"]

    assert [e["start"] for e in entities] == [10.0, 40.0, 8000.0, CHUNK_SECONDS + 5.0, CHUNK_SECONDS + 60.0, CHUNK_SECONDS + 600.0]


def test_each_chunk_gets_its_own_speaker_numbers():
    result = merged_two_chunks()["result"]

    assert [u["speaker"] for u in result["transcription"]["utterances"]] == [0, 1, 0, 2, 3, 2]
    assert [s["speaker"] for s in result["sentences"]["results"]] == [0, 1, 0, 2, 3, 2]


def test_speaker_numbers_keep_climbing_over_three_chunks():
    # Chunk b has speakers 0 and 2 but no 1. Its highest number sets chunk c's
    # base (2 + 2 + 1 = 5); counting its speakers would give 4, and chunk c's
    # speaker 0 would then share number 4 with chunk b's speaker 2.
    chunks = [
        chunk_result(gladia_chunk([utterance(0, 1.0, 3), utterance(1, 9.0, 3)], duration=CHUNK_SECONDS, job_id="a"), 0.0),
        chunk_result(gladia_chunk([utterance(0, 1.0, 3), utterance(2, 9.0, 3)], duration=CHUNK_SECONDS, job_id="b"), CHUNK_SECONDS),
        chunk_result(gladia_chunk([utterance(2, 1.0, 3), utterance(0, 9.0, 3)], duration=600.0, job_id="c"), 2 * CHUNK_SECONDS),
    ]
    result = transcriber._merge_chunk_results(chunks)["result"]

    assert [u["speaker"] for u in result["transcription"]["utterances"]] == [0, 1, 2, 4, 7, 5]
    assert [chunk["speaker_base"] for chunk in result["stormiq_chunks"]] == [0, 2, 5]


def test_the_chunk_map_is_written():
    assert merged_two_chunks()["result"]["stormiq_chunks"] == [
        {"index": 1, "offset_s": 0.0, "duration_s": CHUNK_SECONDS, "speaker_base": 0, "gladia_job_id": "job-1"},
        {"index": 2, "offset_s": CHUNK_SECONDS, "duration_s": 1200.0, "speaker_base": 2, "gladia_job_id": "job-2"},
    ]


def test_billing_and_processing_times_are_summed_and_languages_joined():
    merged = merged_two_chunks()
    metadata = merged["result"]["metadata"]

    assert metadata["billing_time"] == 9300
    assert metadata["transcription_time"] == 360
    assert metadata["audio_duration"] == CHUNK_SECONDS + 1200.0
    assert merged["file"]["audio_duration"] == CHUNK_SECONDS + 1200.0
    assert merged["result"]["transcription"]["languages"] == ["en", "fr"]


def test_a_chunk_level_words_list_is_shifted_and_renumbered_too():
    chunks = two_chunks()
    for chunk in chunks:
        transcription = chunk["gladia_result"]["result"]["transcription"]
        transcription["words"] = [
            {**word, "speaker": u["speaker"]} for u in transcription["utterances"] for word in u["words"]
        ]
    words = transcriber._merge_chunk_results(chunks)["result"]["transcription"]["words"]

    first_of_chunk_two = next(word for word in words if word["start"] >= CHUNK_SECONDS)
    assert first_of_chunk_two["start"] == CHUNK_SECONDS + 5.0
    assert first_of_chunk_two["speaker"] == 2


def test_transcription_level_sentences_and_word_speakers_are_shifted_too():
    # Gladia is moving sentences under `transcription`, and classic reads them
    # there first. Here chunk 1 has no result-level sentences, its words carry
    # a speaker, and speaker 2 is heard only in one of its sentences.
    chunks = two_chunks()
    for chunk in chunks:
        result = chunk["gladia_result"]["result"]
        transcription = result["transcription"]
        for u in transcription["utterances"]:
            for word in u["words"]:
                word["speaker"] = u["speaker"]
        transcription["sentences"] = copy.deepcopy(result["sentences"]["results"])
    first = chunks[0]["gladia_result"]["result"]
    first["sentences"] = {"success": True, "is_empty": True, "results": []}
    late = utterance(2, 8090.0, 3)
    first["transcription"]["sentences"].append(
        {"sentence": late["text"], "start": late["start"], "end": late["end"], "speaker": 2, "words": late["words"]}
    )

    result = transcriber._merge_chunk_results(chunks)["result"]
    transcription = result["transcription"]

    sentences = transcription["sentences"]
    assert [s["start"] for s in sentences] == [10.0, 40.0, 8000.0, 8090.0, CHUNK_SECONDS + 5.0, CHUNK_SECONDS + 60.0, CHUNK_SECONDS + 600.0]
    assert [s["speaker"] for s in sentences] == [0, 1, 0, 2, 3, 4, 3]
    assert sentences[4]["words"][0]["start"] == CHUNK_SECONDS + 5.0
    assert [u["speaker"] for u in transcription["utterances"]] == [0, 1, 0, 3, 4, 3]
    assert all(word["speaker"] == u["speaker"] for u in transcription["utterances"] for word in u["words"])
    assert [chunk["speaker_base"] for chunk in result["stormiq_chunks"]] == [0, 3]

    result_sentences = result["sentences"]
    assert result_sentences["is_empty"] is False
    assert [s["start"] for s in result_sentences["results"]] == [CHUNK_SECONDS + 5.0, CHUNK_SECONDS + 60.0, CHUNK_SECONDS + 600.0]
    assert [s["speaker"] for s in result_sentences["results"]] == [3, 4, 3]


def test_list_shaped_result_blocks_stay_lists():
    chunks = two_chunks()
    for chunk in chunks:
        result = chunk["gladia_result"]["result"]
        result["named_entity_recognition"] = result["named_entity_recognition"]["results"]

    entities = transcriber._merge_chunk_results(chunks)["result"]["named_entity_recognition"]

    assert isinstance(entities, list)
    assert [e["start"] for e in entities] == [10.0, 40.0, 8000.0, CHUNK_SECONDS + 5.0, CHUNK_SECONDS + 60.0, CHUNK_SECONDS + 600.0]


def test_the_merge_leaves_the_chunk_results_untouched():
    chunks = two_chunks()
    before = copy.deepcopy(chunks)

    transcriber._merge_chunk_results(chunks)

    assert chunks == before


def test_a_single_chunk_is_unchanged_apart_from_its_chunk_map():
    payload = gladia_chunk([utterance(0, 1.0, 3), utterance(1, 9.0, 3)], duration=600.0, job_id="only", billing_time=600)
    before = copy.deepcopy(payload)

    merged = transcriber._merge_chunk_results([chunk_result(payload, 0.0)])

    assert payload == before
    assert merged["result"].pop("stormiq_chunks") == [
        {"index": 1, "offset_s": 0.0, "duration_s": 600.0, "speaker_base": 0, "gladia_job_id": "only"},
    ]
    assert merged == before


# ── What classic shows ─────────────────────────────────────────────────────


def normalized(gladia_payload):
    return transcriber._normalize_transcription(
        gladia_payload,
        source_video=Path("recording.mp4"),
        extracted_audio=None,
        requested_model=transcriber.DEFAULT_MODEL,
        actual_model=transcriber.DEFAULT_MODEL,
        session_id="s1",
    )


def test_classic_insights_gain_word_level_pauses_and_confidence_on_a_long_recording():
    insights = build_transcript_insights(normalized(merged_two_chunks()))
    words_df = insights["words_df"]
    silence_df = insights["silence_df"]
    summary = insights["summary"]

    # Every word, on the recording's clock.
    assert len(words_df) == 30
    assert words_df["start_seconds"].min() == 10.0
    assert words_df["start_seconds"].max() > CHUNK_SECONDS + 600.0

    # Pauses come from the words: the hesitation inside chunk 2's first
    # utterance (invisible at segment level) and the silence across the
    # chunk boundary, which ends at chunk 2's first word.
    assert summary["pause_timing_source"] == "word"
    hesitation_start = CHUNK_SECONDS + 5.0 + 3 * WORD_SECONDS + 2 * WORD_GAP_SECONDS
    hesitations = silence_df[silence_df["pause_type"] == "Hesitation"]
    assert hesitations["silence_start_seconds"].tolist() == [pytest.approx(hesitation_start)]
    assert (silence_df["silence_end_seconds"] == CHUNK_SECONDS + 5.0).sum() == 1
    assert not insights["pause_type_df"].empty
    assert not insights["pause_distribution_df"].empty

    # Word confidence: 15 words at 0.9, 15 at 0.8.
    assert summary["avg_confidence"] == pytest.approx(0.85)
    confidence_df = insights["confidence_distribution_df"]
    counts = dict(zip(confidence_df["confidence_bin"].astype(str), confidence_df["count"], strict=True))
    assert counts["0.8-1.0"] == 30

    # Chunk 2's speakers are not chunk 1's.
    assert sorted(insights["speaker_df"]["speaker"]) == ["0", "1", "2", "3"]
