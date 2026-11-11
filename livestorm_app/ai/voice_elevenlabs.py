"""Surprise me's main voice: ElevenLabs text to speech (`eleven_v3`), one
clip (mp3) per line of the performance script.

- **The voice.** `DEFAULT_VOICE_ID` is "Finley" (a deep British male voice
  from ElevenLabs' library), the owner's choice; `CHRIS_VOICE_ID`, the
  premade "Chris", is offered too. Admin may save any voice id
  (`elevenlabs_voice`, checked by `VOICE_ID_PATTERN`).
- **The tone.** `eleven_v3` reads audio tags: each line is sent as
  `"[tag] line"`, the tag from its tone (`TONE_TAGS`). The stability is
  `STABILITY` ("Natural", one of the three v3 takes).
- **The call** (`synthesize`): one POST per line with a timeout, retried
  only for answers that mean nothing was made (429, 5xx, a connection
  error); never after a read timeout. A refusal for the account (no key,
  401, 402, 403, or a quota or payment status in the answer) raises
  `VoiceUnavailable`, which the show job answers by recording with the
  backup voice (OpenAI). No error holds the key or ElevenLabs' answer.

No database, no web framework.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Tuple

import requests

from livestorm_app.ai.surprise_voice import (
    RETRY_AFTER_CAP_S,
    RETRY_STATUSES,
    SPEECH_ATTEMPTS,
    SPEECH_TIMEOUT,
    SpeechFailed,
)
from livestorm_app.config import ELEVENLABS_TTS_URL

logger = logging.getLogger(__name__)

PROVIDER = "elevenlabs"
MODEL = "eleven_v3"
# Finley - Articulate Anchor: male, deep, older, British (en-GB).
DEFAULT_VOICE_ID = "fnYMz3F5gMEDGMWcH1ex"
# Chris: ElevenLabs' premade American male voice, the rehearsal's first pick.
CHRIS_VOICE_ID = "iP95p4xoKVk53GoZ742B"
VOICE_LABELS = {DEFAULT_VOICE_ID: "Finley (British, deep)", CHRIS_VOICE_ID: "Chris (American)"}
VOICE_ID_PATTERN = r"^[A-Za-z0-9]{10,40}$"
OUTPUT_FORMAT = "mp3_44100_128"
STABILITY = 0.5  # v3 takes 0.0 (Creative), 0.5 (Natural) or 1.0 (Robust)

# A line's tone as an eleven_v3 audio tag, said before the line.
TONE_TAGS = {
    "deadpan": "[deadpan]",
    "sarcastic": "[sarcastic]",
    "curious": "[curious]",
    "building": "[excited]",
    "punchline": "[dry]",
    "aside": "[casually]",
    "warm": "[warmly]",
    "excited": "[excited]",
}

# The answers that mean the account can't be used now (ElevenLabs puts a
# `detail.status` in its error body): record with the backup voice instead.
ACCOUNT_STATUSES = frozenset({401, 402, 403})
ACCOUNT_DETAILS = frozenset({
    "quota_exceeded", "payment_required", "invalid_api_key", "missing_permissions",
    "subscription_required", "insufficient_credits", "detected_unusual_activity",
})


class VoiceUnavailable(SpeechFailed):
    """ElevenLabs can't be used for this show (no key, or refused for the
    account: auth, quota, payment). The show job records with the backup."""


def voice_label(voice_id: str) -> str:
    return VOICE_LABELS.get(str(voice_id or ""), str(voice_id or ""))


def tagged_text(say: str, tone: str) -> str:
    """The text sent for one line: its tone's audio tag, then the line."""
    tag = TONE_TAGS.get(str(tone or ""), "")
    return f"{tag} {say}".strip()


def _detail_status(response: requests.Response) -> str:
    try:
        body: Any = response.json()
    except ValueError:
        return ""
    detail = body.get("detail") if isinstance(body, dict) else None
    status = detail.get("status") if isinstance(detail, dict) else None
    return str(status or "").strip().lower()


def _retry_after_s(response: requests.Response, attempt: int) -> float:
    try:
        value = float(response.headers.get("Retry-After") or "")
    except ValueError:
        value = float(2 ** attempt)
    return max(0.0, min(value, RETRY_AFTER_CAP_S))


def synthesize(
    api_key: str,
    text: str,
    settings: Dict[str, str],
    *,
    timeout: Tuple[float, float] = SPEECH_TIMEOUT,
    attempts: int = SPEECH_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """One line read aloud by ElevenLabs (`POST /v1/text-to-speech/{voice}`):
    the mp3's bytes. `text` is already tagged (`tagged_text`)."""
    key = str(api_key or "").strip()
    if not key:
        raise VoiceUnavailable("The main voice isn't set up on this server (no ElevenLabs key).")
    url = ELEVENLABS_TTS_URL.format(voice_id=settings["voice"])
    payload = {"text": text, "model_id": settings["model"], "voice_settings": {"stability": STABILITY}}
    headers = {"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"}
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            response = requests.post(
                url, headers=headers, params={"output_format": OUTPUT_FORMAT}, json=payload, timeout=timeout
            )
        except requests.exceptions.ReadTimeout:
            raise SpeechFailed("ElevenLabs took too long to read a line aloud.") from None
        except requests.exceptions.ConnectionError:
            if last:
                raise SpeechFailed("ElevenLabs could not be reached for the voice.") from None
            sleep(float(2 ** attempt))
            continue
        except requests.RequestException:
            raise SpeechFailed("The call to ElevenLabs for the voice failed.") from None
        if not response.ok and (response.status_code in ACCOUNT_STATUSES or _detail_status(response) in ACCOUNT_DETAILS):
            raise VoiceUnavailable(f"ElevenLabs refused the account (HTTP {response.status_code}).")
        if response.status_code in RETRY_STATUSES and not last:
            sleep(_retry_after_s(response, attempt))
            continue
        if not response.ok:
            # Its `detail.status` is one of ElevenLabs' own codes (never text
            # we sent): logged to tell a busy plan from an outage.
            logger.warning("ElevenLabs answered HTTP %s (status %s)", response.status_code, _detail_status(response) or "none")
            raise SpeechFailed(f"ElevenLabs refused to make the voice (HTTP {response.status_code}).")
        audio = response.content or b""
        if not audio:
            raise SpeechFailed("ElevenLabs sent back no audio for a line.")
        return audio
    raise SpeechFailed("ElevenLabs could not make the voice.")
