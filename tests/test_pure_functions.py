"""Pure-function tests: parsing, progress stages, AI-job gating, metrics, PDFs, the LLM request, Livestorm payloads and pagers.

No database, Redis or network. These pin down behaviour that silently
breaks user-visible output when it regresses.
"""

from __future__ import annotations

import copy
import json

import pandas as pd
import pytest

from livestorm_app import api_logic, llm_client, pdf, progress, worker
from livestorm_app.ai import generation, llm_inputs, prompts
from livestorm_app.analytics import audience, cross_source, session_overview
from livestorm_app.analytics import transcript as transcript_analytics
from livestorm_app.livestorm import client as livestorm_client
from livestorm_app.livestorm import payloads as livestorm_payloads

from fake_anthropic import FakeAnthropic, install


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
    bundle = generation.parse_content_repurpose_bundle_response(response_text)
    assert bundle == {"summary": "S", "blog": "B", "email": "E", "social_media": "M"}
    assert generation._bundle_has_all_sections(bundle)


@pytest.mark.parametrize(
    "response_text",
    ['{"summary": "S", "blog": "B", "email": "E", "social_media": "cut off mid', "[1, 2, 3]", "not json at all", ""],
    ids=["truncated", "not-an-object", "prose", "empty"],
)
def test_unparseable_repurpose_responses_yield_an_incomplete_bundle(response_text):
    bundle = generation.parse_content_repurpose_bundle_response(response_text)
    assert set(bundle) == {"summary", "blog", "email", "social_media"}
    assert not generation._bundle_has_all_sections(bundle)


def test_bundle_with_a_blank_section_is_incomplete():
    assert not generation._bundle_has_all_sections({"summary": "S", "blog": "B", "email": "  ", "social_media": "M"})


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
    monkeypatch.setattr(api_logic, "fetch_session_fields", lambda sid, fields, *, organization_id: rows.get(sid))
    monkeypatch.setattr(
        api_logic,
        "enqueue_unique_job_sync",
        lambda *args, **kwargs: enqueued.append(args) or {"jobId": args[1], "jobStatus": "pending", "enqueued": True},
    )
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
    assert enqueued == [("run_overall_analysis_job", "overall_analysis:s1:French", "s1", "French")]


def test_cached_language_returns_the_workspace_without_enqueueing(ai_job_backend):
    rows, enqueued = ai_job_backend
    rows["s1"] = {"analysis_bundle": {"English": "# Analysis"}}
    assert "outputs" in api_logic.start_overall_analysis("s1", "English", organization_id="org-a")
    assert enqueued == []


def test_partial_repurposing_bundle_is_not_a_cache_hit():
    partial = {"content_repurpose_bundle": {"English": {"summary": "S", "blog": "B", "email": "E"}}}
    assert api_logic._extract_cached_ai_body(partial, "content_repurposing", language="English") is None


# ── Transcript metrics: pause classification (docs/product.md §13) ─────────


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
    silence = transcript_analytics.build_transcript_insights(payload)["silence_df"]
    assert list(silence["gap_seconds"]) == [0.8, 1.5, 2.5]
    assert list(silence["pause_type"]) == ["Thinking pause", "Hesitation", "Strong silence"]
    assert list(silence["speaker_transition"]) == [True, False, True]


# ── Prompts ────────────────────────────────────────────────────────────────


def test_content_repurposing_prompt_is_loaded_from_prompts_folder():
    # A missing prompt file loads as "", which would send the model no
    # instructions at all: the file must exist and ask for all four keys.
    prompt = prompts.build_content_repurpose_bundle_prompt()
    assert prompts.CONTENT_REPURPOSE_BUNDLE_PROMPT_PATH.name == "content_repurpose_bundle_prompt.txt"
    for key in ("summary", "blog", "email", "social_media"):
        assert f"- {key}" in prompt
    assert prompt.endswith("Do not wrap the JSON in code fences.")


# ── Audience: chat from the team and guest speakers is not audience ────────


def _chat_payload(messages: list) -> dict:
    return {
        "data": [
            {"id": f"m{i}", "type": "chat_messages", "attributes": attributes}
            for i, attributes in enumerate(messages)
        ]
    }


def _message(author: str, created_at: int, text: str = "Hello", **flags) -> dict:
    return {"author_id": author, "created_at": created_at, "text_content": text, **flags}


MIXED_CHAT = _chat_payload([
    _message("host", 1_700_000_000, "Link in the chat", from_team_member=True, from_guest_speaker=False),
    _message("host", 1_700_000_060, "Reminder: replay tomorrow", from_team_member=True, from_guest_speaker=False),
    _message("speaker", 1_700_000_120, "Thanks all", from_team_member=False, from_guest_speaker=True),
    _message("ana", 1_700_000_180, "Great point", from_team_member=False, from_guest_speaker=False),
    _message("ben", 1_700_000_240, "Agreed", from_team_member=False, from_guest_speaker=False),
])


def test_chat_frame_keeps_the_team_flags():
    chat_df = audience.build_chat_df_from_payload(MIXED_CHAT)
    assert len(chat_df.index) == 5, "the message list keeps every message"
    assert list(chat_df["from_team_member"]) == [True, True, False, False, False]
    assert list(chat_df["from_guest_speaker"]) == [False, False, True, False, False]


def test_cached_chat_without_flags_counts_as_audience():
    chat_df = audience.build_chat_df_from_payload(_chat_payload([_message("ana", 1_700_000_000)]))
    audience_df, excluded = audience.split_audience_chat(chat_df)
    assert len(audience_df.index) == 1 and excluded == 0


def test_chat_engagement_stats_are_audience_only():
    chat_df = audience.build_chat_df_from_payload(MIXED_CHAT)
    stats = llm_inputs.build_derived_stats(chat_df=chat_df)["chat"]
    assert stats["total_messages"] == 2
    assert stats["unique_authors"] == 2
    assert stats["excluded_team_messages"] == 3
    assert set(stats["top_authors_by_message_count"]) == {"ana", "ben"}


def test_compact_chat_for_the_model_marks_team_messages():
    chat_df = audience.build_chat_df_from_payload(MIXED_CHAT)
    messages = llm_inputs.build_compact_chat_payload_for_llm(chat_df)["messages"]
    assert len(messages) == 5
    assert messages[0]["from_team_member"] is True and messages[3]["from_team_member"] is False


# ── Questions: oral answers count as answered ──────────────────────────────


def _questions_payload(*questions: dict) -> dict:
    return {
        "data": [
            {"id": f"q{i}", "type": "questions", "attributes": {"question": f"Question {i}?", "created_at": 1_700_000_000 + i, **attrs}}
            for i, attrs in enumerate(questions)
        ]
    }


def test_oral_answer_uses_the_spec_spelling_and_counts_as_answered():
    payload = _questions_payload(
        {"response_orally": True},                   # spec spelling, answered live
        {"responded_orally": True},                  # older spelling still read
        {"response_author_id": "p9", "response": "Yes"},
        {"response_orally": False},
    )
    questions_df = audience.build_questions_df_from_payload(payload)
    assert list(questions_df["answered_orally"]) == [True, True, False, False]
    assert not {"response_orally", "responded_orally"} & set(questions_df.columns)
    stats = audience.build_question_stats(questions_df)
    assert stats["answered_questions"] == 3
    assert stats["unanswered_questions"] == 1


def test_deep_analysis_questions_read_the_spec_spelling():
    payload = _questions_payload({"response_orally": True}, {"responded_orally": True}, {})
    events = llm_inputs.build_deep_analysis_questions_payload_for_llm(payload)["question_events"]
    assert [event["responded_orally"] for event in events] == [True, True, None]


# ── Cross-source timeline ──────────────────────────────────────────────────


def _transcript(duration: float = 100.0) -> dict:
    segments = [_segment(float(start), float(start) + 8.0, "A") for start in range(0, int(duration), 10)]
    return {"transcript": {"duration_seconds": duration, "segments": segments}}


def test_the_last_message_and_question_stay_on_the_timeline():
    chat_df = audience.build_chat_df_from_payload(_chat_payload([
        _message("ana", 1_700_000_000), _message("ben", 1_700_000_050), _message("cid", 1_700_000_100),
    ]))
    questions_df = audience.build_questions_df_from_payload(_questions_payload({}, {}))
    timeline = cross_source.build_cross_source_insights(chat_df, questions_df, _transcript())["combined_timeline_df"]
    assert timeline["chat_messages"].sum() == 3
    assert timeline["question_count"].sum() == 2
    assert timeline["bucket_start_pct"].max() == 90


def test_team_chat_is_not_an_audience_reaction():
    chat_df = audience.build_chat_df_from_payload(MIXED_CHAT)
    questions_df = audience.build_questions_df_from_payload(_questions_payload({}))
    timeline = cross_source.build_cross_source_insights(chat_df, questions_df, _transcript())["combined_timeline_df"]
    assert timeline["chat_messages"].sum() == 2


# ── Session overview ───────────────────────────────────────────────────────


def _person(person_id: str, role: str, messages: int, *, attended: bool, country: str, guest: bool = False) -> dict:
    return {
        "id": person_id,
        "type": "people",
        "attributes": {
            "role": role,
            "first_name": person_id,
            "messages_count": messages,
            "registrant_detail": {"attended": attended, "is_guest_speaker": guest, "ip_country_name": country},
        },
    }


def test_most_engaged_people_and_countries_are_about_the_audience():
    payload = {
        "data": {"id": "s1", "attributes": {"registrants_count": 6, "attendees_count": 3}},
        "included": [
            _person("moderator", "team_member", 40, attended=True, country="France"),
            _person("guest", "participant", 30, attended=True, country="France", guest=True),
            _person("ana", "participant", 5, attended=True, country="Spain"),
            _person("ben", "participant", 1, attended=False, country="France"),
            _person("cid", "participant", 0, attended=False, country="France"),
            _person("dee", "participant", 0, attended=False, country="Italy"),
        ],
    }
    overview = session_overview.build_session_overview_data(payload)
    assert list(overview["engagement_top_df"]["person_id"]) == ["ana", "ben", "cid", "dee"]
    country = overview["country_df"].set_index("ip_country_name")
    assert list(country.index) == ["France", "Spain"], "countries are ranked by attendees; Italy had none"
    assert country.loc["France", "attendees"] == 2 and country.loc["France", "people_count"] == 4


# ── PDF ────────────────────────────────────────────────────────────────────


def test_pdf_renders_bold_and_drops_separator_lines():
    from reportlab.platypus import HRFlowable, Image, Paragraph

    story = pdf.build_pdf_story("# Title\n\nThis is **important** text.\n\n---\n\n- **Owner**: Ana", "Deep Analysis")
    assert not any(isinstance(item, Image) for item in story), "no logo image file heads the PDF"
    texts = [item.text for item in story if isinstance(item, Paragraph)]
    assert "This is <b>important</b> text." in texts
    assert "&bull; <b>Owner</b>: Ana" in texts
    assert not any("**" in text or text.strip() == "---" for text in texts)
    assert any(isinstance(item, HRFlowable) for item in story)


def test_pdf_has_the_cobrand_lockup_and_the_unofficial_notice(monkeypatch):
    import reportlab.rl_config

    monkeypatch.setattr(reportlab.rl_config, "pageCompression", 0)  # readable content streams
    data = pdf.analysis_markdown_to_pdf_bytes("Hello **world**", "Smart Recap")
    assert data.startswith(b"%PDF")
    assert b"(Storm) Tj" in data and b"(IQ) Tj" in data and b"(for) Tj" in data
    # The Livestorm logo is the one picture: its pixels plus its transparency mask.
    assert data.count(b"/Subtype /Image") == 2 and b"/SMask" in data
    assert b"not an official Livestorm product" in data


def test_pdf_lockup_logo_is_the_brand_kit_file():
    from PIL import Image

    with Image.open(pdf.LIVESTORM_LOGO_PNG) as logo:
        assert logo.mode == "RGBA"
        assert abs(logo.width / logo.height - pdf.LIVESTORM_LOGO_RATIO) < 0.01


# ── LLM client: temperature only where the model accepts it ────────────────


@pytest.mark.parametrize(
    ("model", "sends_temperature"),
    [
        ("claude-haiku-4-5-20251001", True),
        ("claude-sonnet-4-6", True),
        ("claude-opus-4-6", True),
        ("claude-opus-4-20250514", True),
        ("claude-3-5-sonnet-20241022", True),
        ("claude-opus-4-7", False),
        ("claude-opus-4-8", False),
        ("claude-opus-5", False),
        ("claude-sonnet-5", False),
        ("claude-opus-5-5", False),
        ("claude-sonnet-5-5", False),
        ("claude-haiku-5-5", False),
        ("claude-fable-5-1", False),
        ("some-future-model", False),
    ],
)
def test_temperature_is_sent_only_to_models_that_accept_it(monkeypatch, model, sends_temperature):
    fake = FakeAnthropic([{"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}])
    install(monkeypatch, fake)
    result = llm_client._anthropic_send("key", model, [{"role": "user", "content": "Hi"}], 0.2, 100)
    assert (result.text, result.truncated) == ("ok", False)
    sent = fake.body(0)  # the JSON body `llm_client._post_message` wrote
    assert ("temperature" in sent) is sends_temperature
    if sends_temperature:
        assert sent["temperature"] == 0.2


def test_no_model_takes_temperature_from_api_version_2026_10_21():
    assert llm_client.anthropic_accepts_temperature("claude-sonnet-4-6", "2023-06-01")
    assert not llm_client.anthropic_accepts_temperature("claude-sonnet-4-6", "2026-10-21")
    assert not llm_client.anthropic_accepts_temperature("claude-haiku-4-5-20251001", "2027-01-01")


def test_the_temperature_rule_follows_the_api_version_the_app_sends(monkeypatch):
    from livestorm_app import config

    monkeypatch.setattr(config, "ANTHROPIC_API_VERSION", "2026-10-21")
    assert not llm_client.anthropic_accepts_temperature("claude-sonnet-4-6")


# ── Session stats and the model's people summary are about the audience ────


def test_session_chat_count_and_top_chatters_leave_the_team_out():
    payload = {
        "data": {"id": "s1", "attributes": {"registrants_count": 3, "attendees_count": 3}},
        "included": [
            _person("moderator", "team_member", 40, attended=True, country="France"),
            _person("guest", "participant", 30, attended=True, country="France", guest=True),
            _person("ana", "participant", 5, attended=True, country="Spain"),
        ],
    }
    stats = session_overview.build_session_stats(payload)
    assert stats["total_messages_count"] == 5
    assert stats["team_messages_count"] == 70
    compact = session_overview.build_compact_session_payload_for_llm(payload)
    assert [row["full_name"] for row in compact["participant_summary"]["top_chatters"]] == ["ana"]


# ── Internal sessions: when the team is the room, everyone counts ─────────


def _people(*rows: tuple) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["role", "attended", "is_guest_speaker"])


@pytest.mark.parametrize(
    ("people_df", "internal"),
    [
        (pd.DataFrame(), False),
        (None, False),
        (pd.DataFrame({"attended": [True, True]}), False),
        (_people(("team_member", True, False), ("team_member", True, False)), True),
        (_people(("team_member", True, False), ("participant", True, False)), False),
        (_people(*[("team_member", True, False)] * 2, ("participant", True, False), *[("participant", False, False)] * 5), True),
        (_people(*[("team_member", True, False)] * 2, *[("participant", True, False)] * 3), False),
        (_people(("team_member", False, False), ("team_member", False, False), ("participant", False, False)), True),
        (_people(("team_member", None, False), ("team_member", None, False), ("participant", None, False)), True),
        (pd.DataFrame({"role": ["team_member", "team_member", "participant"]}), True),
        (_people(("team_member", "true", "false"), ("team_member", "true", "false"), ("participant", "false", "false")), True),
        (_people(("team_member", "false", "false"), ("team_member", "false", "false"), ("participant", "true", "false")), False),
        (_people(("team_member", True, True), ("team_member", True, "true"), ("team_member", True, False)), False),
        (_people(("guest_speaker", True, True), ("team_member", True, False), ("participant", True, False)), False),
    ],
    ids=[
        "empty", "not-a-frame", "no-role-column", "all-team", "team-exactly-half",
        "team-most-attendees-few-registrants", "team-under-half-of-attendees",
        "nobody-attended-so-registrants", "attendance-unknown-so-registrants", "no-attended-column",
        "string-guest-speaker-flags", "string-attended-flags", "guest-speakers-are-not-team", "guest-speaker-role",
    ],
)
def test_internal_session_rule(people_df, internal):
    assert audience.is_internal_session(people_df) is internal


def test_internal_session_keeps_every_message():
    chat_df = audience.build_chat_df_from_payload(MIXED_CHAT)
    audience_df, excluded = audience.split_audience_chat(chat_df, internal=True)
    assert len(audience_df.index) == 5 and excluded == 0
    assert bool(audience.audience_chat_mask(chat_df, internal=True).all())
    audience_df, excluded = audience.split_audience_chat(chat_df, internal=False)
    assert len(audience_df.index) == 2 and excluded == 3, "a public session still leaves the team out"


ALL_TEAM_SESSION = {
    "data": {"id": "s1", "attributes": {"registrants_count": 4, "attendees_count": 3}},
    "included": [
        _person("ana", "team_member", 6, attended=True, country="France"),
        _person("ben", "team_member", 3, attended=True, country="Spain"),
        _person("cid", "team_member", 1, attended=True, country="France"),
        _person("dee", "team_member", 0, attended=False, country="Italy"),
    ],
}

ALL_TEAM_CHAT = _chat_payload([
    _message("ana", 1_700_000_000, "Welcome everyone", from_team_member=True, from_guest_speaker=False),
    _message("ben", 1_700_000_060, "Great quarter", from_team_member=True, from_guest_speaker=False),
    _message("ana", 1_700_000_120, "Questions at the end", from_team_member=True, from_guest_speaker=False),
])


def test_all_team_session_counts_everyone_in_the_overview():
    overview = session_overview.build_session_overview_data(ALL_TEAM_SESSION)
    assert overview["internal_session"] is True
    assert list(overview["engagement_top_df"]["person_id"]) == ["ana", "ben", "cid", "dee"]
    assert overview["stats"]["total_messages_count"] == 10
    assert overview["stats"]["team_messages_count"] == 0
    compact = session_overview.build_compact_session_payload_for_llm(ALL_TEAM_SESSION)
    assert [row["full_name"] for row in compact["participant_summary"]["top_chatters"]] == ["ana", "ben", "cid", "dee"]


def test_all_team_session_gives_the_model_all_chat_and_says_it_is_internal():
    chat_df = audience.build_chat_df_from_payload(ALL_TEAM_CHAT)
    stats = llm_inputs.build_derived_stats(chat_df=chat_df, session_payload=ALL_TEAM_SESSION)
    assert stats["internal_session"] is True
    assert stats["chat"]["total_messages"] == 3
    assert stats["chat"]["unique_authors"] == 2
    assert stats["chat"]["excluded_team_messages"] == 0
    # The session summary counts the chat itself, not the people's
    # `messages_count` (which add up to 10 here).
    assert stats["session"]["total_messages_count"] == 3
    assert stats["session"]["team_messages_count"] == 0
    messages = llm_inputs.build_compact_chat_payload_for_llm(chat_df)["messages"]
    assert len(messages) == 3 and all(message["from_team_member"] is True for message in messages)


def test_internal_timeline_counts_team_chat():
    chat_df = audience.build_chat_df_from_payload(ALL_TEAM_CHAT)
    questions_df = audience.build_questions_df_from_payload(_questions_payload({}))
    internal = cross_source.build_cross_source_insights(chat_df, questions_df, _transcript(), internal=True)
    assert internal["combined_timeline_df"]["chat_messages"].sum() == 3
    assert internal["reaction_moments_df"]["chat_messages"].sum() == 3
    public = cross_source.build_cross_source_insights(chat_df, questions_df, _transcript(), internal=False)
    assert public["combined_timeline_df"]["chat_messages"].sum() == 0


def _timeline_chat(workspace: dict) -> float:
    return sum(row["chat_messages"] for row in workspace["tables"]["chatQuestionsTimeline"])


def test_all_team_workspace_carries_the_decision_for_the_spa():
    workspace = api_logic._serialize_cached_session("s1", {
        "session_payload": ALL_TEAM_SESSION,
        "chat_payload": ALL_TEAM_CHAT,
        "questions_payload": _questions_payload({}),
        "transcript_payload": _transcript(),
    })
    assert workspace["meta"]["internalSession"] is True
    assert workspace["stats"]["chat"]["total_messages"] == 3
    assert len(workspace["tables"]["engagementTop"]) == 4
    assert _timeline_chat(workspace) == 3


# A typical public webinar: a host and a moderator, a guest speaker, 20
# attendees out of 50 registrants. Nothing changes for it.
PUBLIC_WEBINAR = {
    "data": {"id": "s2", "attributes": {"registrants_count": 50, "attendees_count": 20}},
    "included": [
        _person("host", "team_member", 12, attended=True, country="France"),
        _person("moderator", "team_member", 8, attended=True, country="France"),
        _person("speaker", "participant", 4, attended=True, country="Spain", guest=True),
        *[_person(f"p{i:02d}", "participant", 1 if i < 5 else 0, attended=i < 17, country="Italy") for i in range(47)],
    ],
}


def test_public_webinar_still_leaves_the_team_out():
    overview = session_overview.build_session_overview_data(PUBLIC_WEBINAR)
    assert overview["internal_session"] is False
    assert overview["stats"]["total_messages_count"] == 5
    assert overview["stats"]["team_messages_count"] == 24
    assert not {"host", "moderator", "speaker"} & set(overview["engagement_top_df"]["person_id"])
    chat_df = audience.build_chat_df_from_payload(MIXED_CHAT)
    stats = llm_inputs.build_derived_stats(chat_df=chat_df, session_payload=PUBLIC_WEBINAR)
    assert "internal_session" not in stats
    assert stats["chat"]["total_messages"] == 2 and stats["chat"]["excluded_team_messages"] == 3
    # The session summary counts the whole chat: 2 audience, 3 team messages.
    assert (stats["session"]["total_messages_count"], stats["session"]["team_messages_count"]) == (2, 3)
    assert stats == llm_inputs.build_derived_stats(chat_df=chat_df, session_payload=PUBLIC_WEBINAR, internal=False)
    workspace = api_logic._serialize_cached_session("s2", {
        "session_payload": PUBLIC_WEBINAR,
        "chat_payload": MIXED_CHAT,
        "questions_payload": _questions_payload({}),
        "transcript_payload": _transcript(),
    })
    assert workspace["meta"]["internalSession"] is False
    assert workspace["stats"]["chat"]["total_messages"] == 2
    assert _timeline_chat(workspace) == 2


def _run_ai_job(monkeypatch, run, session_payload, chat_payload) -> dict:
    """Runs an AI job on a cached row and returns what the model was sent."""
    row = {
        "session_payload": session_payload,
        "chat_payload": chat_payload,
        "questions_payload": _questions_payload({}),
        "transcript_payload": _transcript(),
    }
    seen: dict = {}
    monkeypatch.setattr(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: dict(row))
    monkeypatch.setattr(api_logic, "merge_cached_bundle_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(api_logic, "get_llm_model", lambda role: "test-model")
    monkeypatch.setattr(api_logic, "analyze_with_openai", lambda **kwargs: seen.update(kwargs) or "# Analysis")
    run("llm-key", "s1", "English")
    return seen


# The classic inputs; the Session analysis reads the session's brief instead (tests/test_session_brief.py).
@pytest.mark.parametrize("run", [api_logic.run_deep_analysis], ids=["deep"])
@pytest.mark.parametrize(
    ("session_payload", "chat_payload", "internal", "excluded", "team"),
    [
        (ALL_TEAM_SESSION, ALL_TEAM_CHAT, True, 0, {"ana", "ben", "cid", "dee"}),
        (PUBLIC_WEBINAR, MIXED_CHAT, False, 3, {"host", "moderator", "speaker"}),
    ],
    ids=["internal", "public"],
)
def test_ai_jobs_tell_the_model_who_the_audience_is(monkeypatch, run, session_payload, chat_payload, internal, excluded, team):
    seen = _run_ai_job(monkeypatch, run, session_payload, chat_payload)
    stats = seen["derived_stats"]
    assert stats.get("internal_session", False) is internal
    assert stats["chat"]["excluded_team_messages"] == excluded
    top_chatters = {row["full_name"] for row in seen["session_payload"]["participant_summary"]["top_chatters"]}
    assert bool(team & top_chatters) is internal


def test_overall_analysis_sees_questions_answered_orally():
    payload = _questions_payload({"response_orally": True}, {})
    questions_df = audience.build_questions_df_from_payload(payload)
    rows = llm_inputs.build_compact_questions_payload_for_llm(questions_df)["questions"]
    assert [row["answered_orally"] for row in rows] == [True, False]


def test_the_last_bucket_is_right_when_the_size_does_not_divide_100():
    assert cross_source._bucket_start(100.0, 10) == 90
    assert cross_source._bucket_start(100.0, 15) == 90
    assert cross_source._bucket_start(95.0, 15) == 90
    assert cross_source._bucket_start(50.0, 15) == 45
    assert cross_source._bucket_start(float("nan"), 10) is None


# ── Chat Messages counts the chat, not the people's messages_count ────────

# An all-hands where Livestorm reports `messages_count` 0 for every person
# although the chat has messages.
ZERO_COUNT_ALL_HANDS = {
    "data": {"id": "s3", "attributes": {"registrants_count": 3, "attendees_count": 3}},
    "included": [
        _person("ana", "team_member", 0, attended=True, country="France"),
        _person("ben", "team_member", 0, attended=True, country="Spain"),
        _person("cid", "team_member", 0, attended=True, country="France"),
    ],
}


def _workspace(session_payload, chat_payload, questions_payload=None, transcript_payload=None) -> dict:
    return api_logic._serialize_cached_session("s1", {
        "session_payload": session_payload,
        "chat_payload": chat_payload,
        "questions_payload": questions_payload,
        "transcript_payload": transcript_payload,
    })


def _message_counts(workspace: dict) -> tuple:
    stats = workspace["stats"]["sessionOverview"]
    return stats["total_messages_count"], stats["team_messages_count"]


def test_chat_messages_counts_the_chat_when_every_person_says_zero():
    chat_df = audience.build_chat_df_from_payload(ALL_TEAM_CHAT)
    overview = session_overview.build_session_overview_data(ZERO_COUNT_ALL_HANDS, chat_df=chat_df)
    assert overview["internal_session"] is True
    assert overview["stats"]["total_messages_count"] == 3
    assert overview["stats"]["team_messages_count"] == 0
    assert _message_counts(_workspace(ZERO_COUNT_ALL_HANDS, ALL_TEAM_CHAT)) == (3, 0)


def test_chat_messages_leaves_team_chat_out_of_a_public_session():
    # The people say 5 audience and 24 team messages; the chat has 2 and 3.
    assert _message_counts(_workspace(PUBLIC_WEBINAR, MIXED_CHAT)) == (2, 3)


def test_chat_messages_falls_back_to_the_people_without_a_cached_chat():
    assert _message_counts(_workspace(PUBLIC_WEBINAR, None)) == (5, 24)


def test_an_empty_cached_chat_counts_no_message():
    assert _message_counts(_workspace(PUBLIC_WEBINAR, _chat_payload([]))) == (0, 0)


def test_the_model_sees_the_chat_count_in_the_session_summary():
    chat_df = audience.build_chat_df_from_payload(ALL_TEAM_CHAT)
    stats = llm_inputs.build_derived_stats(chat_df=chat_df, session_payload=ZERO_COUNT_ALL_HANDS)
    assert stats["session"]["total_messages_count"] == stats["chat"]["total_messages"] == 3


# The classic inputs; the Session analysis reads the session's brief instead (tests/test_session_brief.py).
@pytest.mark.parametrize("run", [api_logic.run_deep_analysis], ids=["deep"])
def test_ai_jobs_count_an_empty_cached_chat_as_no_message(monkeypatch, run):
    # The people say 5 audience and 24 team messages; the cached chat has none.
    session = _run_ai_job(monkeypatch, run, PUBLIC_WEBINAR, _chat_payload([]))["derived_stats"]["session"]
    assert (session["total_messages_count"], session["team_messages_count"]) == (0, 0)



# ── Cross-source timeline: chat or questions is enough ─────────────────────


def test_a_chat_only_session_gets_its_timeline_and_reaction_moments():
    chat_df = audience.build_chat_df_from_payload(ALL_TEAM_CHAT)
    insights = cross_source.build_cross_source_insights(chat_df, None, _transcript(), internal=True)
    timeline = insights["combined_timeline_df"]
    assert len(timeline.index) == 10
    assert timeline["chat_messages"].sum() == 3 and timeline["question_count"].sum() == 0
    moments = insights["reaction_moments_df"]
    assert moments["chat_messages"].sum() == 3 and moments["question_count"].sum() == 0


def test_a_questions_only_session_gets_its_timeline():
    questions_df = audience.build_questions_df_from_payload(_questions_payload({}, {}))
    timeline = cross_source.build_cross_source_insights(None, questions_df, _transcript())["combined_timeline_df"]
    assert timeline["question_count"].sum() == 2 and timeline["chat_messages"].sum() == 0


def test_no_timeline_without_a_transcript_or_without_chat_and_questions():
    chat_df = audience.build_chat_df_from_payload(ALL_TEAM_CHAT)
    for insights in (
        cross_source.build_cross_source_insights(chat_df, None, None),
        cross_source.build_cross_source_insights(None, None, _transcript()),
    ):
        assert insights["combined_timeline_df"].empty and insights["reaction_moments_df"].empty


def test_a_chat_only_workspace_has_its_timeline_and_reaction_moments():
    workspace = _workspace(ZERO_COUNT_ALL_HANDS, ALL_TEAM_CHAT, _questions_payload(), _transcript())
    assert _timeline_chat(workspace) == 3
    assert sum(row["question_count"] for row in workspace["tables"]["chatQuestionsTimeline"]) == 0
    moments = workspace["tables"]["chatQuestionReactionMoments"]
    assert sum(row["chat_messages"] for row in moments) == 3


# ── Event picker: only a real session count is shown ──────────────────────

_NO_COUNT = object()


def _event(sessions_count) -> dict:
    attributes = {"title": "Quarterly update", "updated_at": 1_700_000_000}
    if sessions_count is not _NO_COUNT:
        attributes["sessions_count"] = sessions_count
    return {"id": "e1", "type": "events", "attributes": attributes}


@pytest.mark.parametrize(
    ("sessions_count", "label_count", "field"),
    [
        (3, " (3 sessions)", "3"),
        (0, " (0 sessions)", "0"),
        ("4", " (4 sessions)", "4"),
        (-2, "", "n/a"),
        ("-1", "", "n/a"),
        (None, "", "n/a"),
        (_NO_COUNT, "", "n/a"),
        (True, "", "n/a"),
        (2.5, "", "n/a"),
        ("many", "", "n/a"),
    ],
    ids=["count", "zero", "digit-string", "negative", "negative-string", "null", "missing", "bool", "float", "text"],
)
def test_event_picker_shows_only_a_real_session_count(sessions_count, label_count, field):
    [option] = livestorm_payloads.build_workspace_event_options({"data": [_event(sessions_count)]})
    assert option["label"] == f"Quarterly update{label_count} · 2023-11-14 22:13 UTC"
    assert option["sessions_count"] == field


# ── PDF details ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line", "markup"),
    [
        ("***Key point***", "<b><i>Key point</i></b>"),
        ("A **bold** and ***both*** line", "A <b>bold</b> and <b><i>both</i></b> line"),
        ("Fish & <chips>", "Fish &amp; &lt;chips&gt;"),
    ],
)
def test_pdf_inline_markup(line, markup):
    assert pdf.inline_markdown_to_markup(line) == markup


def test_french_pdfs_carry_the_notice_in_french():
    assert pdf.footer_text("French") == pdf.PDF_FOOTER_TEXT_FR
    assert pdf.footer_text("English") == pdf.PDF_FOOTER_TEXT
    assert "pas un produit officiel Livestorm" in pdf.PDF_FOOTER_TEXT_FR


# ── Content repurposing: the empty-transcript placeholder is never content ──


PLACEHOLDER_BUNDLE = {key: "Transcript is empty." for key in ("summary", "blog", "email", "social_media")}


def test_a_placeholder_bundle_is_incomplete():
    assert not generation._bundle_has_all_sections(PLACEHOLDER_BUNDLE)
    assert generation.missing_bundle_sections(PLACEHOLDER_BUNDLE) == ["summary", "blog", "email", "social_media"]


def test_a_stored_placeholder_bundle_counts_as_missing():
    real = {"summary": "S", "blog": "B", "email": "E", "social_media": "M"}
    cached = {"content_repurpose_bundle": {"English": PLACEHOLDER_BUNDLE, "French": real}}
    assert api_logic._extract_cached_ai_body(cached, "content_repurposing", language="English") is None
    assert api_logic._extract_cached_ai_body(cached, "content_repurposing", language="French") == "ready"
    assert api_logic._serialize_outputs(cached)["contentRepurposeBundle"] == {"French": real}


# ── Cover images: a shut-down model is replaced ────────────────────────────


@pytest.mark.parametrize(
    ("saved", "sent"),
    [("gpt-image-1", "gpt-image-2"), ("gpt-image-2", "gpt-image-2"), ("", "gpt-image-2")],
)
def test_a_retired_image_model_is_replaced(saved, sent):
    from livestorm_app.ai import cover_image

    assert cover_image.resolve_image_model(saved) == sent


# ── Livestorm payloads: registrant join links are never kept ───────────────
#
# Synthetic payloads in the shapes the API returns: the session with
# `include=people`, chat messages, questions with `include=asker,responder`,
# and a page of people.

JOIN_LINK_KEYS = ("password_key", "connection_link")


def _registrant(person_id: str, *, join_link: bool = True) -> dict:
    detail = {
        "attended": True,
        "attendance_rate": 80,
        "fields": [{"id": "company", "type": "text", "value": "Example Co"}],
        "referrer": "https://example.test/landing",
        "registration_type": "detailed",
    }
    if join_link:
        detail["password_key"] = f"pk-{person_id}"
        detail["connection_link"] = f"https://example.test/join/{person_id}?key=pk-{person_id}"
    return {
        "id": person_id,
        "type": "people",
        "attributes": {"first_name": "Test", "last_name": person_id, "role": "participant", "registrant_detail": detail},
        "relationships": {},
    }


def _session_payload() -> dict:
    return {
        "data": {"id": "s1", "type": "sessions", "attributes": {"event_id": "e1", "status": "past", "registrants_count": 3}},
        "included": [_registrant("p1"), _registrant("p2", join_link=False), {"id": "p3", "type": "people", "attributes": {}}],
    }


def _chat_payload_with_people() -> dict:
    return {
        "data": [{"id": "m1", "type": "chat_messages", "attributes": {"text": "Hello", "author_id": "p1", "created_at": 1700000000}}],
        "included": [_registrant("p1")],
        "meta": {"current_page": 0, "next_page": None},
        "pages_fetched": 1,
    }


def _questions_payload_with_people() -> dict:
    return {
        "data": [
            {
                "id": "q1",
                "type": "questions",
                "attributes": {"question": "Why?", "question_author_id": "p1", "created_at": 1700000100},
                "relationships": {"asker": {"data": {"id": "p1", "type": "people"}}},
            }
        ],
        "included": [_registrant("p1"), _registrant("p4")],
        "pages_fetched": 1,
    }


def _people_page() -> dict:
    return {"data": [_registrant("p5"), _registrant("p6", join_link=False)], "meta": {"record_count": 2, "next_page": None}}


def _one_person() -> dict:
    return {"data": _registrant("p7")}


def _expected_without_join_links(payload: dict) -> dict:
    expected = copy.deepcopy(payload)
    for section in ("data", "included"):
        value = expected.get(section)
        for record in value if isinstance(value, list) else [value]:
            detail = ((record or {}).get("attributes") or {}).get("registrant_detail")
            if isinstance(detail, dict):
                for key in JOIN_LINK_KEYS:
                    detail.pop(key, None)
    return expected


PAYLOAD_SHAPES = pytest.mark.parametrize(
    "build",
    [_session_payload, _chat_payload_with_people, _questions_payload_with_people, _people_page, _one_person],
    ids=["session", "chat", "questions", "people-page", "one-person"],
)


@PAYLOAD_SHAPES
def test_the_strip_removes_join_links_and_keeps_every_other_key(build):
    payload = build()
    stripped = livestorm_payloads.strip_payload_join_links(payload)
    assert stripped == _expected_without_join_links(payload)
    assert not any(key in json.dumps(stripped) for key in JOIN_LINK_KEYS)


@PAYLOAD_SHAPES
def test_the_strip_is_pure_and_idempotent(build):
    payload = build()
    original = copy.deepcopy(payload)
    once = livestorm_payloads.strip_payload_join_links(payload)
    assert payload == original, "the input must not be changed"
    twice = livestorm_payloads.strip_payload_join_links(once)
    assert twice == once
    assert twice is once, "a clean payload comes back as the same object"


@pytest.mark.parametrize("payload", [None, "text", 3, {}, {"data": None}, {"data": [None, "x"]}, [], {"meta": {"next_page": None}}])
def test_the_strip_leaves_anything_without_people_alone(payload):
    assert livestorm_payloads.strip_payload_join_links(payload) is payload


def test_the_record_strip_ignores_records_without_a_registrant_detail():
    for record in ({"id": "p1"}, {"attributes": None}, {"attributes": {"registrant_detail": "n/a"}}, _registrant("p1", join_link=False)):
        assert livestorm_payloads.strip_registrant_join_link(record) is record


# ── Livestorm pagers: 50 per page, following meta.next_page ────────────────


class _FakeLivestormResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _fake_livestorm(monkeypatch, pages: dict, *, included: dict | None = None) -> list:
    """Serve `pages[number] = (item_count, next_page)` and record each request's params.

    A `next_page` of `...` serves the page without pagination metadata.
    """
    calls: list = []

    def fake_get(url, *, headers, params=None):
        calls.append(dict(params or {}))
        number = (params or {}).get("page[number]", 0)
        count, next_page = pages[number]
        payload = {"data": [{"id": f"r{number}-{i}", "type": "chat_messages", "attributes": {}} for i in range(count)]}
        if next_page is not ...:
            payload["meta"] = {"current_page": number, "next_page": next_page}
        if included and number in included:
            payload["included"] = included[number]
        return _FakeLivestormResponse(payload)

    monkeypatch.setattr(livestorm_client, "livestorm_get", fake_get)
    return calls


LIVESTORM_PAGERS = pytest.mark.parametrize(
    "pager",
    [livestorm_client.fetch_chat_messages, livestorm_client.fetch_session_questions, livestorm_client.fetch_event_past_sessions],
    ids=["chat", "questions", "event-sessions"],
)


@LIVESTORM_PAGERS
@pytest.mark.parametrize(
    ("pages", "expected_numbers", "expected_items"),
    [
        ({0: (50, 1), 1: (50, 2), 2: (3, None)}, [0, 1, 2], 103),
        ({0: (50, None)}, [0], 50),
        ({0: (50, ...), 1: (10, ...)}, [0, 1], 60),
    ],
    ids=["follows-next-page", "stops-when-next-page-is-null", "no-metadata-stops-on-a-short-page"],
)
def test_every_pager_asks_for_50_and_follows_next_page(monkeypatch, pager, pages, expected_numbers, expected_items):
    calls = _fake_livestorm(monkeypatch, pages)
    payload = pager("Bearer t", "id-1")
    assert [call["page[number]"] for call in calls] == expected_numbers
    assert {call["page[size]"] for call in calls} == {50}
    assert len(payload["data"]) == expected_items
    assert payload["pages_fetched"] == len(expected_numbers)
    assert payload["requested_page_size"] == 50


def test_the_questions_pager_strips_join_links_from_every_page(monkeypatch):
    calls = _fake_livestorm(
        monkeypatch,
        {0: (50, 1), 1: (2, None)},
        included={0: [_registrant("p1")], 1: [_registrant("p2")]},
    )
    payload = livestorm_client.fetch_session_questions("Bearer t", "s1")
    assert {call["page[size]"] for call in calls} == {50}
    assert {call["include"] for call in calls} == {"asker,responder"}
    assert sorted(person["id"] for person in payload["included"]) == ["p1", "p2"]
    assert not any(key in json.dumps(payload) for key in JOIN_LINK_KEYS)
    assert payload["included"][0]["attributes"]["registrant_detail"]["fields"][0]["value"] == "Example Co"


def test_session_details_come_back_without_join_links(monkeypatch):
    monkeypatch.setattr(livestorm_client, "livestorm_get", lambda url, *, headers, params=None: _FakeLivestormResponse(_session_payload()))
    payload = livestorm_client.fetch_session_details("Bearer t", "s1")
    assert payload == _expected_without_join_links(_session_payload())


@pytest.mark.parametrize(("requested", "sent"), [(100, 50), (50, 50), (20, 20)])
def test_no_pager_asks_livestorm_for_more_than_50(monkeypatch, requested, sent):
    calls = _fake_livestorm(monkeypatch, {0: (0, None)})
    livestorm_client.fetch_chat_messages("Bearer t", "s1", page_size=requested)
    livestorm_client.fetch_session_questions("Bearer t", "s1", page_size=requested)
    livestorm_client.fetch_event_past_sessions("Bearer t", "e1", page_size=requested)
    livestorm_client.fetch_workspace_events_page("Bearer t", page_size=requested)
    assert [call["page[size]"] for call in calls] == [sent] * 4


# ── Gladia: the speaker cap ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("people_on_stage", "max_speakers"),
    [
        (1, 3),  # 1 + 2 is the floor too: with a margin of 2 the floor never binds
        (2, 4),
        (5, 7),
        (18, 20),
        (19, 20),  # the ceiling
        (60, 20),
        (None, 20),  # unknown keeps the ceiling
        (0, 20),
        (-1, 20),
        (True, 20),  # not a count
        ("3", 20),
    ],
)
def test_the_speaker_cap_is_people_on_stage_plus_two_between_3_and_20(people_on_stage, max_speakers):
    from livestorm_app.gladia import transcriber

    assert transcriber.max_speakers_for(people_on_stage) == max_speakers
    assert transcriber.speaker_options(people_on_stage) == {
        "diarization_config": {"min_speakers": 1, "max_speakers": max_speakers}
    }


def test_the_speaker_cap_floor_binds_when_the_margin_is_smaller(monkeypatch):
    from livestorm_app.gladia import transcriber

    monkeypatch.setattr(transcriber, "MAX_SPEAKERS_MARGIN", 0)
    assert [transcriber.max_speakers_for(count) for count in (1, 2, 3, 4)] == [3, 3, 3, 4]


def test_the_speaker_cap_ceiling_is_the_default_request():
    from livestorm_app.gladia import transcriber

    assert transcriber.DEFAULT_GLADIA_OPTIONS["diarization_config"]["max_speakers"] == transcriber.MAX_SPEAKERS_CEILING == 20
    request = transcriber._build_gladia_request("https://gladia.example/a", transcriber.speaker_options(2))
    assert request["diarization_config"] == {"min_speakers": 1, "max_speakers": 4}


def test_event_session_options_carry_the_raw_facts_next_to_the_label():
    payload = {
        "data": [
            {"id": "s-old", "attributes": {"name": "", "started_at": 1_700_000_000, "attendees_count": 12}},
            {"id": "s-new", "attributes": {"name": "Spring launch", "started_at": "1700090000", "attendees_count": -3}},
            {"id": "s-none", "attributes": {"estimated_started_at": 0, "attendees_count": "7"}},
        ]
    }
    options = livestorm_payloads.build_event_session_options(payload)
    assert [option["id"] for option in options] == ["s-new", "s-old", "s-none"]
    by_id = {option["id"]: option for option in options}
    # The label is unchanged; the new fields say the same thing unformatted.
    assert by_id["s-old"]["label"] == "2023-11-14 22:13 UTC - Untitled session (12 attendees)"
    assert by_id["s-old"] | {"label": None} == {"id": "s-old", "label": None, "name": "", "startedAt": 1_700_000_000, "attendees": 12}
    # A negative count is not a count; a start of 0 is unknown.
    assert by_id["s-new"]["name"] == "Spring launch"
    assert by_id["s-new"]["startedAt"] == 1_700_090_000
    assert by_id["s-new"]["attendees"] is None
    assert by_id["s-none"]["startedAt"] is None
    assert by_id["s-none"]["attendees"] == 7
