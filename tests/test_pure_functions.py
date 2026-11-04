"""Pure-function tests: parsing, progress stages, AI-job gating, transcript metrics.

No database, Redis or network. These pin down behaviour that silently
breaks user-visible output when it regresses.
"""

from __future__ import annotations

import pytest

from livestorm_app import api_logic, progress, services, worker


# ── Content repurposing: LLM JSON bundle parsing ──────────────────────────

FULL_BUNDLE = '{"summary": "S", "blog": "B", "email": "E", "social_media": "M"}'


@pytest.mark.parametrize(
    "response_text",
    [
        FULL_BUNDLE,
        f"```json\n{FULL_BUNDLE}\n```",
        f"Here is your bundle:\n{FULL_BUNDLE}\nHope it helps!",
    ],
    ids=["bare", "fenced", "prose-wrapped"],
)
def test_repurpose_bundle_is_extracted_from_common_llm_shapes(response_text):
    bundle = services.parse_content_repurpose_bundle_response(response_text)
    assert bundle == {"summary": "S", "blog": "B", "email": "E", "social_media": "M"}
    assert services._bundle_has_all_sections(bundle)


@pytest.mark.parametrize(
    "response_text",
    ['{"summary": "S", "blog": "B", "email": "E", "social_media": "cut off mid', "[1, 2, 3]", "not json at all", ""],
    ids=["truncated", "not-an-object", "prose", "empty"],
)
def test_unparseable_repurpose_responses_yield_an_incomplete_bundle(response_text):
    bundle = services.parse_content_repurpose_bundle_response(response_text)
    assert set(bundle) == {"summary", "blog", "email", "social_media"}
    assert not services._bundle_has_all_sections(bundle)


def test_bundle_with_a_blank_section_is_incomplete():
    assert not services._bundle_has_all_sections({"summary": "S", "blog": "B", "email": "  ", "social_media": "M"})


# ── Progress stages ───────────────────────────────────────────────────────


@pytest.mark.parametrize("table", [progress.TRANSCRIPT_STAGE_FLOORS, progress.ANALYSIS_STAGE_FLOORS], ids=["transcript", "analysis"])
def test_stage_floors_rise_from_0_to_100(table):
    floors = list(table.values())
    assert floors[0] == 0 and floors[-1] == 100
    assert floors == sorted(floors) and len(set(floors)) == len(floors), "a progress bar must never go backwards"


@pytest.mark.parametrize(
    "step,stage",
    [
        ("downloading", "fetching_recording"),
        ("extracting", "fetching_recording"),
        ("Uploading", "uploading_to_gladia"),
        ("merging", "post_processing"),
        ("queued", "transcribing"),
        ("something-new", "transcribing"),
        (None, "transcribing"),
    ],
)
def test_gladia_steps_map_to_known_transcript_stages(step, stage):
    mapped = worker._map_gladia_step_to_stage(step)
    assert mapped == stage
    assert mapped in progress.TRANSCRIPT_STAGE_FLOORS


def test_unknown_progress_kind_floors_at_zero():
    assert progress._resolve_floor("no-such-kind", "analyzing") == 0


# ── AI jobs: cache hits and English-first gating ──────────────────────────


@pytest.fixture
def ai_job_backend(monkeypatch):
    enqueued: list = []
    rows: dict = {}
    monkeypatch.setattr(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: rows.get(sid))
    monkeypatch.setattr(api_logic, "enqueue_job_sync", lambda *args, **kwargs: enqueued.append(args) or "job-1")
    monkeypatch.setattr(api_logic, "get_in_flight_ai_job_id", lambda *a, **k: None)
    monkeypatch.setattr(api_logic, "set_in_flight_ai_job_id", lambda *a, **k: None)
    monkeypatch.setattr(api_logic, "_serialize_cached_session", lambda sid, cached: {"sessionId": sid, "outputs": {}})
    return rows, enqueued


def test_french_analysis_requires_english_first(ai_job_backend):
    rows, enqueued = ai_job_backend
    rows["s1"] = {"analysis_bundle": {}}
    with pytest.raises(RuntimeError, match="English version first"):
        api_logic.start_overall_analysis("s1", "French", organization_id="org-a")
    assert enqueued == []


def test_french_analysis_is_enqueued_once_english_exists(ai_job_backend):
    rows, enqueued = ai_job_backend
    rows["s1"] = {"analysis_bundle": {"English": "# Analysis"}}
    response = api_logic.start_overall_analysis("s1", "French", organization_id="org-a")
    assert response["jobStatus"] == "pending" and response["language"] == "French"
    assert enqueued == [("run_overall_analysis_job", "s1", "French")]


def test_cached_language_returns_the_workspace_without_enqueueing(ai_job_backend):
    rows, enqueued = ai_job_backend
    rows["s1"] = {"analysis_bundle": {"English": "# Analysis"}}
    assert "outputs" in api_logic.start_overall_analysis("s1", "English", organization_id="org-a")
    assert enqueued == []


def test_partial_repurposing_bundle_is_not_a_cache_hit():
    partial = {"content_repurpose_bundle": {"English": {"summary": "S", "blog": "B", "email": "E"}}}
    assert api_logic._extract_cached_ai_body(partial, "content_repurposing", language="English") is None


# ── Transcript metrics: pause classification (CLAUDE.md §7) ────────────────


def _segment(start: float, end: float, speaker: str) -> dict:
    return {"start": start, "end": end, "speaker": speaker, "text": "We are discussing the quarterly results today"}


def test_pauses_are_classified_by_gap_length():
    # Gaps: 0.5 s (below the 0.75 s detection floor), 0.8 s, 1.5 s, 2.5 s.
    payload = {
        "transcript": {
            "duration_seconds": 17.0,
            "segments": [
                _segment(0.0, 2.0, "A"),
                _segment(2.5, 5.0, "A"),
                _segment(5.8, 8.0, "B"),
                _segment(9.5, 12.0, "B"),
                _segment(14.5, 17.0, "A"),
            ],
        }
    }
    silence = services.build_transcript_insights(payload)["silence_df"]
    assert list(silence["gap_seconds"]) == [0.8, 1.5, 2.5]
    assert list(silence["pause_type"]) == ["Thinking pause", "Hesitation", "Strong silence"]
    assert list(silence["speaker_transition"]) == [True, False, True]
