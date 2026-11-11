"""A transcript speaker's display name: the one rule every page uses.

Outside `record/` so that code which must not load the v1 record (the
workspace's outputs, `ai.decisions_entry`) words speakers the same way as the
Transcript tab (`record.transcript_view`, which imports it from here).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


def speaker_name(key: Optional[str], saved: Mapping[str, Any], order: int) -> str:
    """The name the user saved for a speaker, else "Speaker n" (n from 1, in
    order of first appearance)."""
    label = saved.get(key) if key is not None else None
    if isinstance(label, str) and label.strip():
        return label.strip()
    return f"Speaker {order}"
