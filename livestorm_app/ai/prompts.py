"""System prompts for the classic AI outputs, built from the text files in
prompts/. The Session analysis has its own (`ai/session_analysis.py`)."""

from __future__ import annotations

from pathlib import Path

from livestorm_app.config import (
    ANALYSIS_DEEP_PROMPT_PATH,
    CONTENT_REPURPOSE_BUNDLE_PROMPT_PATH,
    SMART_RECAP_HYPE_PROMPT_PATH,
    SMART_RECAP_PROFESSIONAL_PROMPT_PATH,
    SMART_RECAP_SURPRISE_PROMPT_PATH,
)


def load_analysis_prompt(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def build_deep_analysis_prompt() -> str:
    prompt = load_analysis_prompt(ANALYSIS_DEEP_PROMPT_PATH)
    if prompt.strip():
        return prompt
    return (
        "You are a staff-level technical analyst. Perform a deep analytical review of the provided Livestorm "
        "session data using the full transcript JSON, chat payload, and questions payload. Return a technical "
        "markdown report with sections for executive summary, timeline analysis, communication quality, speaker "
        "dynamics, engagement diagnostics, business signal extraction, risk areas, data limitations, and highly "
        "specific recommendations. Support conclusions with concrete evidence from the payloads."
    )

def build_content_repurpose_bundle_prompt() -> str:
    return load_analysis_prompt(CONTENT_REPURPOSE_BUNDLE_PROMPT_PATH)


def build_smart_recap_prompt(tone: str) -> str:
    prompt_map = {
        "professional": SMART_RECAP_PROFESSIONAL_PROMPT_PATH,
        "hype": SMART_RECAP_HYPE_PROMPT_PATH,
        "surprise": SMART_RECAP_SURPRISE_PROMPT_PATH,
    }
    prompt_path = prompt_map.get(tone)
    if prompt_path is not None:
        prompt = load_analysis_prompt(prompt_path)
        if prompt.strip():
            return prompt
    return (
        "You create short transcript-only session recaps. Return markdown with exactly `# Title` and "
        "`# Description`. Use only the transcript text, avoid outside facts, and match the requested tone."
    )
