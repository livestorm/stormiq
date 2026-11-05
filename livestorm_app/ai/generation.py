"""Text generation through the LLM client: analyses, Smart Recap, content repurposing and translation."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from livestorm_app.ai.prompts import build_content_repurpose_bundle_prompt
from livestorm_app.llm_client import call_llm

logger = logging.getLogger(__name__)


def _extract_json_object_from_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced_match:
        return fenced_match.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start:end + 1].strip()
    return cleaned


def parse_content_repurpose_bundle_response(response_text: str) -> Dict[str, str]:
    empty_bundle = {"summary": "", "blog": "", "email": "", "social_media": ""}
    json_text = _extract_json_object_from_text(response_text)
    if not json_text:
        return empty_bundle
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return empty_bundle
    if not isinstance(payload, dict):
        return empty_bundle
    return {
        key: str(payload.get(key) or "").strip()
        for key in ["summary", "blog", "email", "social_media"]
    }


# Text that earlier versions saved for an empty transcript. It is never
# content: a section holding it counts as missing.
EMPTY_TRANSCRIPT_PLACEHOLDER = "Transcript is empty."


def _is_placeholder_section(value: Any) -> bool:
    return str(value or "").strip().lower() == EMPTY_TRANSCRIPT_PLACEHOLDER.lower()


def is_placeholder_bundle(bundle: Any) -> bool:
    """Whether a stored bundle is the empty-transcript placeholder, not content."""
    return isinstance(bundle, dict) and any(_is_placeholder_section(value) for value in bundle.values())


def missing_bundle_sections(bundle: Any) -> List[str]:
    """The sections of a content bundle that are absent, blank or placeholder text."""
    bundle = bundle if isinstance(bundle, dict) else {}
    return [
        key
        for key in ["summary", "blog", "email", "social_media"]
        if not isinstance(bundle.get(key), str) or not bundle.get(key, "").strip() or _is_placeholder_section(bundle.get(key))
    ]


def _bundle_has_all_sections(bundle: Dict[str, str]) -> bool:
    return not missing_bundle_sections(bundle)


def _bundle_language_looks_wrong(bundle: Dict[str, str], output_language: str) -> bool:
    output_language = str(output_language or "").strip().lower()
    if output_language != "french":
        return False

    sample_text = " ".join(
        str(bundle.get(key) or "")
        for key in ["summary", "blog", "email", "social_media"]
    ).lower()
    if not sample_text.strip():
        return False

    english_markers = [
        " the ", " and ", " with ", " for ", " your ", " this ", " that ", " you ", " we ",
        " follow-up ", " key takeaways ", " next steps ", " subject line ", " social media ",
    ]
    french_markers = [
        " le ", " la ", " les ", " de ", " des ", " et ", " avec ", " pour ", " votre ", " vos ",
        " ce ", " cette ", " vous ", " nous ", " suivi ", " prochaines etapes ", " points cles ",
    ]

    english_score = sum(sample_text.count(marker) for marker in english_markers)
    french_score = sum(sample_text.count(marker) for marker in french_markers)
    return english_score > max(french_score + 8, 12)


def translate_markdown_with_openai(
    api_key: str,
    model: str,
    source_markdown: str,
    source_language: str,
    target_language: str,
    max_tokens: int = 6000,
) -> str:
    source_markdown = str(source_markdown or "").strip()
    if not source_markdown:
        return ""

    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You translate markdown reports between English and French. Preserve structure, headings, bullets, "
                "tables, timestamps, score values, and factual meaning. Do not summarize, expand, or omit content."
            ),
        },
        {"role": "system", "content": f"Translate from {source_language} to {target_language}. Return only translated markdown."},
        {"role": "user", "content": source_markdown},
    ]
    return call_llm(api_key, model, messages, temperature=0.1, max_tokens=max_tokens)


def translate_content_repurpose_bundle_with_openai(
    api_key: str,
    model: str,
    source_bundle: Dict[str, str],
    source_language: str,
    target_language: str,
) -> Dict[str, str]:
    normalized_bundle = {
        key: str(value or "").strip()
        for key, value in (source_bundle or {}).items()
        if key in {"summary", "blog", "email", "social_media"}
    }
    if not any(normalized_bundle.values()):
        return {"summary": "", "blog": "", "email": "", "social_media": ""}

    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You translate a structured content repurposing JSON bundle between English and French. Return valid JSON "
                "with exactly these keys: summary, blog, email, social_media. Preserve structure, intent, formatting, calls to action, "
                "and platform-appropriate tone. Translate the values only."
            ),
        },
        {"role": "system", "content": f"Translate from {source_language} to {target_language}. Return only JSON."},
        {"role": "user", "content": json.dumps(normalized_bundle, ensure_ascii=False)},
    ]

    bundle = {"summary": "", "blog": "", "email": "", "social_media": ""}
    for attempt in range(3):
        extra_messages: List[Dict[str, str]] = []
        if attempt == 1:
            extra_messages.append(
                {
                    "role": "system",
                    "content": "The previous answer was incomplete. Return valid JSON with all four required keys populated: summary, blog, email, social_media.",
                }
            )
        elif attempt == 2:
            extra_messages.append(
                {
                    "role": "system",
                    "content": f"The previous answer was not fully written in {target_language}. Rewrite all four sections entirely in {target_language}. Do not mix in the source language except unavoidable brand/platform names.",
                }
            )
        text = call_llm(api_key, model, messages + extra_messages, temperature=0.1, max_tokens=12000)
        bundle = parse_content_repurpose_bundle_response(text)
        if _bundle_has_all_sections(bundle) and not _bundle_language_looks_wrong(bundle, target_language):
            return bundle
    return bundle


def generate_content_repurpose_bundle_with_openai(
    api_key: str,
    model: str,
    output_language: str,
    transcript_text: str,
) -> Dict[str, str]:
    transcript_text = str(transcript_text or "").strip()
    if not transcript_text:
        # Fail the job rather than return placeholder text, which would pass
        # the completeness check and be saved as content.
        raise RuntimeError("The transcript is empty, so there is nothing to repurpose. Nothing was saved.")

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": build_content_repurpose_bundle_prompt()},
        {"role": "system", "content": f"Respond only in {output_language}."},
        {"role": "user", "content": f"Transcript text:\n\n{transcript_text}"},
    ]
    bundle = {"summary": "", "blog": "", "email": "", "social_media": ""}
    for attempt in range(3):
        extra_messages: List[Dict[str, str]] = []
        if attempt == 1:
            extra_messages.append(
                {
                    "role": "system",
                    "content": "The previous answer was incomplete. Return valid JSON with all four required keys populated: summary, blog, email, social_media.",
                }
            )
        elif attempt == 2:
            extra_messages.append(
                {
                    "role": "system",
                    "content": f"The previous answer was not fully written in {output_language}. Rewrite all four sections entirely in {output_language}. Do not mix in English except unavoidable brand/platform names.",
                }
            )
        text = call_llm(api_key, model, messages + extra_messages, temperature=0.2, max_tokens=12000)
        bundle = parse_content_repurpose_bundle_response(text)
        if _bundle_has_all_sections(bundle) and not _bundle_language_looks_wrong(bundle, output_language):
            return bundle
        if attempt == 0:
            logger.warning("Content repurposing bundle response was incomplete; retrying once with stricter instructions.")
        elif attempt == 1 and _bundle_language_looks_wrong(bundle, output_language):
            logger.warning("Content repurposing bundle response language looked wrong; retrying with stricter language instructions.")

    return bundle


def analyze_with_openai(
    api_key: str,
    model: str,
    system_prompt: str,
    output_language: str,
    selected_sources: List[str],
    derived_stats: Dict[str, Any],
    raw_payload: Optional[Dict[str, Any]] = None,
    questions_payload: Optional[Dict[str, Any]] = None,
    transcript_payload: Optional[Dict[str, Any]] = None,
    session_payload: Optional[Dict[str, Any]] = None,
    transcript_text: str = "",
    max_tokens: int = 2500,
) -> str:
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": f"Respond only in {output_language}."},
    ]
    user_payload = {
        "task": "Use this Livestorm session data to complete the requested task.",
        "selected_sources": selected_sources,
        "derived_stats": derived_stats,
    }
    transcript_text = str(transcript_text or "").strip()
    if transcript_text:
        messages.append({"role": "user", "content": f"Transcript full text:\n\n{transcript_text}"})
    if raw_payload is not None:
        user_payload["chat_api_response"] = raw_payload
    if questions_payload is not None:
        user_payload["questions_api_response"] = questions_payload
    if transcript_payload is not None:
        user_payload["transcript_api_response"] = transcript_payload
        transcript_segments = transcript_payload.get("segments")
        if isinstance(transcript_segments, list) and transcript_segments:
            readable_segments: List[str] = []
            for segment in transcript_segments[:20]:
                if not isinstance(segment, dict):
                    continue
                speaker = str(segment.get("speaker") or "").strip()
                text = str(segment.get("text") or "").strip()
                start_label = str(segment.get("start_label") or "").strip()
                if not text:
                    continue
                prefix_parts = [part for part in [start_label, speaker] if part]
                prefix = " | ".join(prefix_parts)
                readable_segments.append(f"{prefix}: {text}" if prefix else text)
            if readable_segments:
                user_payload["transcript_excerpt"] = readable_segments
    if session_payload is not None:
        user_payload["session_api_response"] = session_payload

    has_structured_context = any(
        [
            bool(selected_sources),
            bool(derived_stats),
            raw_payload is not None,
            questions_payload is not None,
            transcript_payload is not None,
            session_payload is not None,
        ]
    )
    if has_structured_context:
        messages.append({"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)})

    # An empty answer is returned as-is: callers fail the job on it
    # (api_logic._require_generated_text) rather than saving a placeholder.
    return call_llm(api_key, model, messages, temperature=0.2, max_tokens=max_tokens) or ""
