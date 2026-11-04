"""System prompts for every AI output, built from the text files in prompts/."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

from livestorm_app.config import (
    ANALYSIS_ALL_SOURCES_PROMPT_PATH,
    ANALYSIS_BASE_PROMPT_PATH,
    ANALYSIS_CHAT_PROMPT_PATH,
    ANALYSIS_DEEP_PROMPT_PATH,
    ANALYSIS_QUESTIONS_PROMPT_PATH,
    ANALYSIS_TRANSCRIPT_PROMPT_PATH,
    CONTENT_REPURPOSE_BUNDLE_PROMPT_PATH,
    COVER_IMAGE_PROMPT_PATH,
    SMART_RECAP_HYPE_PROMPT_PATH,
    SMART_RECAP_PROFESSIONAL_PROMPT_PATH,
    SMART_RECAP_SURPRISE_PROMPT_PATH,
)


def load_analysis_prompt(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def build_analysis_prompt(selected_sources: List[str]) -> str:
    prompt_parts = [load_analysis_prompt(ANALYSIS_BASE_PROMPT_PATH)]
    if "chat" in selected_sources:
        prompt_parts.append(load_analysis_prompt(ANALYSIS_CHAT_PROMPT_PATH))
    if "questions" in selected_sources:
        prompt_parts.append(load_analysis_prompt(ANALYSIS_QUESTIONS_PROMPT_PATH))
    if "transcript" in selected_sources:
        prompt_parts.append(load_analysis_prompt(ANALYSIS_TRANSCRIPT_PROMPT_PATH))
    if set(selected_sources) == {"chat", "questions", "transcript"}:
        prompt_parts.append(load_analysis_prompt(ANALYSIS_ALL_SOURCES_PROMPT_PATH))

    prompt = "\n\n".join(part for part in prompt_parts if part.strip())
    if prompt.strip():
        return prompt

    return (
        "You are a senior analyst. Review the selected Livestorm session sources and return concise, "
        "evidence-based markdown with an executive summary, key themes, engagement insights, risks, "
        "and actionable recommendations. Clearly state any limits caused by missing sources."
    )


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


def _extract_recap_title(recap_markdown: str) -> str:
    """Pull the title line out of a Professional Smart Recap.

    The recap prompt locks the output format to:

        # Title
        <actual title goes here>
        # Description
        <body...>

    This helper finds the first non-empty line *after* a `# Title`
    header. Falls back to the first H1 line whose content isn't the
    literal word "title" / "description". Returns "" when nothing
    sensible is found — caller falls back to a generic placeholder
    so the image prompt always has something to render.
    """
    text = str(recap_markdown or "").strip()
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").split("\n")
    # Pass 1: find the line right after a `# Title` (or `## Title`) marker.
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^#+\s*title\s*$", line, re.IGNORECASE):
            for follower in lines[index + 1:]:
                follower = follower.strip()
                if not follower:
                    continue
                if follower.startswith("#"):
                    break
                return follower
    # Pass 2: first H1 whose content isn't a section marker.
    for raw in lines:
        match = re.match(r"^#\s+(.+?)\s*$", raw.strip())
        if match:
            candidate = match.group(1).strip()
            if candidate.lower() not in {"title", "description", "summary"}:
                return candidate
    return ""


def build_cover_image_prompt(summary_markdown: str) -> str:
    """Render the cover-image prompt template with the recap title +
    summary interpolated into the `{title}` and `{summary}` placeholders.

    The template lives in `prompts/cover_image_prompt.txt` — editable
    without redeploying. The recap's own title is forwarded so the
    image model has a concrete phrase to render, instead of falling
    back to a generic "Session Recap" overlay.
    """
    template = load_analysis_prompt(COVER_IMAGE_PROMPT_PATH)
    summary = str(summary_markdown or "").strip() or "(No summary available.)"
    title = _extract_recap_title(summary_markdown) or "Session Cover"
    return template.replace("{title}", title).replace("{summary}", summary)
