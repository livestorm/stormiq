"""Surprise me's voice: the take cut into beats, and one speech clip (mp3)
per line of the performance script, by the main voice (ElevenLabs,
`ai/voice_elevenlabs.py`) or the backup (OpenAI, here).

- **Beats** (`surprise_beats`): the same cut as the player's
  (`frontend/src/followup/surpriseBeats.js`): the take as Follow-up shows it
  (`clean_recap`, the port of `outputs.js` `cleanRecap`), a beat per
  sentence, the title first, the last sentence of each paragraph its
  punchline. `frontend/tests/fixtures/surprise-beats-v1.json` holds takes and
  their beats; both sides are tested against it, so a scene on screen and
  the CC under it are the take's own sentence.
- **The voices** (`voice_plan`): the `voice_provider` setting (`elevenlabs`,
  the default, or `openai`) picks the main voice; with ElevenLabs, OpenAI is
  the backup. ElevenLabs: `eleven_v3`, the `elevenlabs_voice` setting (else
  Finley), the line's tone as an audio tag. OpenAI: `TTS_MODEL`
  (gpt-4o-mini-tts), the `tts_voice` setting (or `TTS_VOICE`, cedar), the
  delivery notes in `prompts/surprise_voice.txt` and the line's tone
  (`TONE_NOTES`). Only a line's text is sent: the script was checked
  against the take (no attendee named, every figure the take's).
- **The cache key** (`clip_key`): a hash of the provider, the model, the
  voice, the notes and exactly what is sent for the line. A replay costs
  nothing; a new script, voice or note makes new clips, on the next click.
- **The call** (`synthesize_speech`, `record_line`): one POST per line with
  a timeout, retried only for answers that mean nothing was made (429, 5xx,
  a connection error); never after a read timeout.

No database, no web framework.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import requests

from livestorm_app.config import (
    OPENAI_SPEECH_URL,
    SURPRISE_VOICE_PROMPT_PATH,
    TTS_MODEL,
    TTS_VOICE,
)

# Bump when the lines, the request or the clip format change: every clip
# key changes with it, so no old clip is played for a new line.
SURPRISE_VOICE_VERSION = 2

VOICE_PROVIDERS = ("elevenlabs", "openai")
DEFAULT_VOICE_PROVIDER = "elevenlabs"
# OpenAI's delivery note for one line, after the voice's own notes.
TONE_NOTES = {
    "deadpan": "This line: deadpan, flat and dry, barely any emphasis.",
    "sarcastic": "This line: sarcastic, the ironic word leaned on, a knowing half-smile.",
    "curious": "This line: genuinely puzzled, a rising question, inviting the listener in.",
    "building": "This line: building momentum, a little faster, the setup of a joke.",
    "punchline": "This line: the punchline. A real pause at the ellipsis, then land it flat and short.",
    "aside": "This line: a quick throwaway aside, lower and faster.",
    "warm": "This line: warm and sincere, slower, a smile you can hear.",
    "excited": "This line: delighted, a burst of energy, but not shouting.",
}

# The voices gpt-4o-mini-tts offers (OpenAI's text-to-speech guide).
TTS_VOICES = (
    "alloy", "ash", "ballad", "cedar", "coral", "echo", "fable", "marin", "nova", "onyx", "sage", "shimmer", "verse",
)
# cedar: a male voice, one of the two newest and most natural OpenAI voices
# (with marin), and the one that follows delivery notes best: dry and wry
# without sounding like an announcer.
DEFAULT_TTS_VOICE = "cedar"
DEFAULT_INSTRUCTIONS = "Dry, wry and warm: a witty friend narrating, deadpan before the punchline, never mocking anyone."

# OpenAI's limit on one speech input, and a bound on one take's beats (a
# take is about 900 tokens: a few dozen sentences).
MAX_BEAT_CHARS = 4096
MAX_BEATS = 80

CLIP_MIME = "audio/mpeg"
CLIP_KEY_PATTERN = r"^[0-9a-f]{64}$"

SPEECH_TIMEOUT = (10, 60)  # connect, read
SPEECH_ATTEMPTS = 3
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_AFTER_CAP_S = 10.0


class SpeechFailed(RuntimeError):
    """A clip could not be made. Its message says what failed, never the key
    or OpenAI's answer: it is what the player shows."""


# ── The beats (port of surpriseBeats.js) ──


def clean_recap(value: Any) -> str:
    """A Smart Recap as Follow-up shows it (`outputs.js` `cleanRecap`): a
    "Title" heading becomes "## <the title>", a "Description" heading goes."""
    lines = str(value if value is not None else "").strip().replace("\r\n", "\n").split("\n")
    out: List[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if re.match(r"^#{1,2}\s*title\s*$", line, re.IGNORECASE):
            following = lines[index + 1].strip() if index + 1 < len(lines) else ""
            if following:
                out.append(f"## {following}")
                index += 1
            index += 1
            continue
        if re.match(r"^#{1,2}\s*description\s*$", line, re.IGNORECASE):
            index += 1
            continue
        out.append(lines[index])
        index += 1
    return "\n".join(out).strip()


def _plain_text(markdown: str) -> str:
    return re.sub(r"\*\*|__|`|\*|_", "", markdown.replace("\r\n", "\n")).strip()


_SENTENCE = re.compile(r"[^.!?…]+(?:[.!?…]+[\"”’)]*|$)")
_HEADING = re.compile(r"^#{1,6}\s+(.*)$")


def _sentences(paragraph: str) -> List[str]:
    return [found.strip() for found in _SENTENCE.findall(paragraph) if found.strip()]


def surprise_beats(take: Any) -> List[Dict[str, str]]:
    """The saved take as beats, `[{kind, text}]`, `kind` title | line | punch,
    exactly as the player cuts it. Empty for an empty take."""
    beats: List[Dict[str, str]] = []
    paragraph: List[str] = []

    def flush() -> None:
        sentences = _sentences(re.sub(r"\s+", " ", " ".join(paragraph)).strip())
        for index, sentence in enumerate(sentences):
            last = index == len(sentences) - 1 and len(sentences) > 1
            beats.append({"kind": "punch" if last else "line", "text": sentence})
        paragraph.clear()

    for raw in _plain_text(clean_recap(take)).split("\n"):
        line = raw.strip()
        heading = _HEADING.match(line)
        if heading:
            flush()
            title = heading.group(1).strip()
            if title:
                beats.append({"kind": "title", "text": title})
        elif not line:
            flush()
        else:
            paragraph.append(line)
    flush()
    return beats


# ── The voice ──


def resolve_voice(saved: Any) -> str:
    """The voice to send: a saved one gpt-4o-mini-tts offers, else cedar."""
    name = str(saved or "").strip().lower()
    return name if name in TTS_VOICES else DEFAULT_TTS_VOICE


def _setting(key: str) -> str:
    try:
        from livestorm_app.db import get_setting

        return str(get_setting(key) or "").strip()
    except Exception:
        return ""


def resolve_provider(saved: Any) -> str:
    name = str(saved or "").strip().lower()
    return name if name in VOICE_PROVIDERS else DEFAULT_VOICE_PROVIDER


def openai_settings(saved_voice: Any = None) -> Dict[str, str]:
    """`{provider, model, voice, notes}` for OpenAI: `TTS_MODEL`, the voice
    (else `TTS_VOICE`), `prompts/surprise_voice.txt`."""
    try:
        notes = SURPRISE_VOICE_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        notes = ""
    return {
        "provider": "openai",
        "model": str(TTS_MODEL or "").strip() or "gpt-4o-mini-tts",
        "voice": resolve_voice(saved_voice or TTS_VOICE),
        "notes": notes or DEFAULT_INSTRUCTIONS,
    }


def elevenlabs_settings(saved_voice: Any = None) -> Dict[str, str]:
    """`{provider, model, voice, notes}` for ElevenLabs: `eleven_v3`, the
    voice id (else Finley); the notes are the stability sent."""
    from livestorm_app.ai import voice_elevenlabs as eleven

    voice = str(saved_voice or "").strip()
    if not re.match(eleven.VOICE_ID_PATTERN, voice):
        voice = eleven.DEFAULT_VOICE_ID
    return {"provider": eleven.PROVIDER, "model": eleven.MODEL, "voice": voice, "notes": f"stability={eleven.STABILITY}"}


def voice_plan() -> Tuple[Dict[str, str], Optional[Dict[str, str]]]:
    """The main voice and its backup (None when the main one is OpenAI's),
    from the Admin settings `voice_provider`, `elevenlabs_voice`, `tts_voice`."""
    backup = openai_settings(_setting("tts_voice"))
    if resolve_provider(_setting("voice_provider")) == "openai":
        return backup, None
    return elevenlabs_settings(_setting("elevenlabs_voice")), backup


def line_request(line: Mapping[str, Any], settings: Mapping[str, str]) -> Dict[str, str]:
    """What is sent for one line: `{text, instructions}`. ElevenLabs gets the
    tone as an audio tag in the text; OpenAI as a note after the voice's."""
    say = " ".join(str(line.get("say") or "").split())
    tone = str(line.get("tone") or "")
    if settings["provider"] == "elevenlabs":
        from livestorm_app.ai.voice_elevenlabs import tagged_text

        return {"text": tagged_text(say, tone), "instructions": ""}
    hint = TONE_NOTES.get(tone, "")
    return {"text": say, "instructions": f"{settings['notes']}\n\n{hint}".strip()}


def clip_key(line: Mapping[str, Any], settings: Mapping[str, str]) -> str:
    """The cache key of one line's clip: sha256 of the version, the provider,
    the model, the voice, the notes and exactly what is sent."""
    sent = line_request(line, settings)
    parts = [
        str(SURPRISE_VOICE_VERSION), settings["provider"], settings["model"], settings["voice"], settings["notes"],
        sent["instructions"], sent["text"],
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def record_line(line: Mapping[str, Any], settings: Mapping[str, str], keys: Mapping[str, str]) -> bytes:
    """One line read aloud by the settings' provider, with its key from
    `keys` (`{"openai": …, "elevenlabs": …}`, read from the worker's env)."""
    sent = line_request(line, settings)
    if settings["provider"] == "elevenlabs":
        from livestorm_app.ai.voice_elevenlabs import synthesize

        return synthesize(keys.get("elevenlabs", ""), sent["text"], dict(settings))
    request = {"model": settings["model"], "voice": settings["voice"], "instructions": sent["instructions"]}
    return synthesize_speech(keys.get("openai", ""), sent["text"], request)


def _retry_after_s(response: requests.Response, attempt: int) -> float:
    try:
        value = float(response.headers.get("Retry-After") or "")
    except ValueError:
        value = float(2 ** attempt)
    return max(0.0, min(value, RETRY_AFTER_CAP_S))


def synthesize_speech(
    api_key: str,
    text: str,
    settings: Dict[str, str],
    *,
    timeout: Tuple[float, float] = SPEECH_TIMEOUT,
    attempts: int = SPEECH_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """One line read aloud by OpenAI (`POST /v1/audio/speech`): the mp3's
    bytes. Retries a 429, a 5xx and a connection error (nothing was made),
    never a read timeout (the clip may be on its way, and paid for)."""
    key = str(api_key or "").strip()
    if not key:
        raise SpeechFailed("The voice isn't set up on this server (no OpenAI key).")
    payload = {
        "model": settings["model"],
        "voice": settings["voice"],
        "input": text,
        "instructions": settings["instructions"],
        "response_format": "mp3",
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            response = requests.post(OPENAI_SPEECH_URL, headers=headers, json=payload, timeout=timeout)
        except requests.exceptions.ReadTimeout:
            raise SpeechFailed("OpenAI took too long to read a sentence aloud.") from None
        except requests.exceptions.ConnectionError:
            # ConnectTimeout is one too: nothing reached OpenAI.
            if last:
                raise SpeechFailed("OpenAI could not be reached for the voice.") from None
            sleep(float(2 ** attempt))
            continue
        except requests.RequestException:
            raise SpeechFailed("The call to OpenAI for the voice failed.") from None
        if response.status_code in RETRY_STATUSES and not last:
            sleep(_retry_after_s(response, attempt))
            continue
        if not response.ok:
            raise SpeechFailed(f"OpenAI refused to make the voice (HTTP {response.status_code}).")
        audio = response.content or b""
        if not audio:
            raise SpeechFailed("OpenAI sent back no audio for a sentence.")
        return audio
    raise SpeechFailed("OpenAI could not make the voice.")


# ── The clip's length ──

_BITRATES_KBPS = {
    3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),  # MPEG-1 Layer III
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),  # MPEG-2 Layer III
    0: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),  # MPEG-2.5 Layer III
}
_SAMPLE_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def mp3_duration_s(data: bytes) -> Optional[float]:
    """An mp3's length in seconds, from its frame headers (an ID3v2 tag
    skipped), or None when no Layer III frame is found. The player times
    each beat by it until the clip's own length is known."""
    view = memoryview(data or b"")
    size = len(view)
    index = 0
    if size >= 10 and bytes(view[:3]) == b"ID3":
        tag = (view[6] & 0x7F) << 21 | (view[7] & 0x7F) << 14 | (view[8] & 0x7F) << 7 | (view[9] & 0x7F)
        index = 10 + tag + (10 if view[5] & 0x10 else 0)
    seconds = 0.0
    frames = 0
    while index + 4 <= size:
        b1, b2 = view[index + 1], view[index + 2]
        version, layer = (b1 >> 3) & 3, (b1 >> 1) & 3
        bitrate_index, rate_index = b2 >> 4, (b2 >> 2) & 3
        if (
            view[index] != 0xFF or (b1 & 0xE0) != 0xE0 or version == 1 or layer != 1
            or bitrate_index in (0, 15) or rate_index == 3
        ):
            index += 1
            continue
        rate = _SAMPLE_RATES[version][rate_index]
        bitrate = _BITRATES_KBPS[version][bitrate_index] * 1000
        mpeg1 = version == 3
        seconds += (1152 if mpeg1 else 576) / rate
        frames += 1
        index += (144 if mpeg1 else 72) * bitrate // rate + ((b2 >> 1) & 1)
    return round(seconds, 3) if frames else None
