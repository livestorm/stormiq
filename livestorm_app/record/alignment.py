"""The alignment probe: where the recording sits on the session clock.

The transcript keeps recording seconds (`r`); chat, questions and answers
are on the session clock (`t`, seconds since `started_at`). They relate as
`t = r + offset`: an offset of +30 means the recording started 30 seconds
after `started_at`. `probe_alignment(...)` estimates that offset from
moments where speech and chat meet, says how far the estimate can be
trusted, and returns one `Alignment`.

Anchors. Each one turns into a range the offset must lie in:

- `link`: a team chat message with a link, paired one-to-one with the
  nearest utterance that announces something in the chat ("the link", "in
  the chat", "je vous mets"). Bounds both sides, loosely;
- `wave`: a call to the chat ("tell us in the chat", "bonjour à tous")
  followed by a burst of audience messages. Speech, then chat: an upper
  bound (the lower edge relies on people reacting quickly);
- `read_aloud`: an audience question or chat message whose words the
  speakers say in one to three consecutive utterances, found through a
  token index. Chat, then speech: a lower bound. Team chat is never read
  aloud, even on an internal session: the one typing may be the one
  speaking, and type the words after saying them;
- `media_time`: the recording file's `creation_time`, behind
  `MEDIA_TIME_ANCHOR`, which stays off until it is shown to mark the
  recording's start;
- `duration`: the recording's length against the session's. A constraint
  on the offset, never an anchor.

One message and one utterance feed at most one anchor (links first, then
waves, then reads aloud), so one moment can't confirm itself. A sweep line
finds the offset range most anchors agree on; the drift check runs it again
on each half of the recording. The status follows:
`verified`, `assumed` (offset 0 applied), `misaligned` or `unavailable`.
`alignment_allows(alignment, resolution)` is the only code that reads the
status: findings in seconds need `verified`, findings in minutes `verified`
or `assumed`, and audience-only views always run.

Every threshold below is provisional, to tune on a study of 5 to 10
sessions checked by ear against their replay
(`scripts/probe_alignment_study.py`); changing one bumps
`ALIGNMENT_VERSION`. The output carries our own ids (`C0001`, `Q001`,
`U0001`) and times only: never a word, a message, a speaker label or a
Livestorm id. Rules: docs/product.md §13, "Alignment probe (v1 Report)".

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import bisect
import math
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Dict, FrozenSet, List, Mapping, NamedTuple, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from livestorm_app.analytics.transcript import COMMON_STOPWORDS
from livestorm_app.record.clock import ClockBase, parse_time
from livestorm_app.record.evidence import CHAT, QUESTION, question_items
from livestorm_app.record.transcript import ParsedTranscript

ALIGNMENT_VERSION = 1

# Statuses. Only `alignment_allows` decides anything from them.
VERIFIED = "verified"
ASSUMED = "assumed"
MISALIGNED = "misaligned"
UNAVAILABLE = "unavailable"
STATUSES = (VERIFIED, ASSUMED, MISALIGNED, UNAVAILABLE)

# What a finding is measured in (`alignment_allows`).
SECONDS = "seconds"
MINUTES = "minutes"
AUDIENCE = "audience"
RESOLUTIONS = (SECONDS, MINUTES, AUDIENCE)
_ALLOWED: Mapping[str, FrozenSet[str]] = {
    SECONDS: frozenset({VERIFIED}),
    MINUTES: frozenset({VERIFIED, ASSUMED}),
}

# Anchor kinds, and the side of the offset each one bounds.
LINK = "link"
WAVE = "wave"
READ_ALOUD = "read_aloud"
MEDIA_TIME = "media_time"
ANCHOR_KINDS = (LINK, WAVE, READ_ALOUD, MEDIA_TIME)
LOWER = "lower"
UPPER = "upper"
BOUNDS: Mapping[str, FrozenSet[str]] = {
    LINK: frozenset({LOWER, UPPER}),
    WAVE: frozenset({UPPER}),
    READ_ALOUD: frozenset({LOWER}),
    MEDIA_TIME: frozenset({LOWER, UPPER}),
}

SOURCE_ANCHORS = "anchors"  # a host's own offset (`host`) comes later
EARLY = "early"
LATE = "late"

# ── Thresholds: provisional, to tune on the ear-checked study ──────────────

# Whether the mp4's `creation_time` marks the recording's start is not
# confirmed yet, so the media_time anchor stays off.
MEDIA_TIME_ANCHOR = False
MEDIA_TIME_SLACK_S = 2.0

SEARCH_BOUND_S = 900.0  # |offset| searched, at least
SEARCH_GAP_MARGIN_S = 120.0  # widened to |session − recording| + this
DURATION_SLACK_S = 30.0  # the duration constraint's margin on each side

LINK_POSTED_BEFORE_S = 30.0  # a link may be posted this long before it is announced
LINK_POSTED_AFTER_S = 90.0  # or this long after

WAVE_WINDOW_S = 20.0  # a burst: this many seconds of audience chat
WAVE_MIN_MESSAGES = 3  # holding at least this many messages
WAVE_BASELINE_FACTOR = 3.0  # and this many times the session's usual rate per window
WAVE_MIN_LAG_S = 3.0  # the burst starts at least this long after the call ends
WAVE_MAX_LAG_S = 45.0  # and at most this long after

READ_MIN_WORDS = 5  # content words a message needs to be matched
READ_MAX_WORDS = 40  # longer messages are not matched
READ_MAX_UTTERANCES = 3  # its words must appear in 1 to 3 consecutive utterances
READ_MIN_RECALL = 0.7  # share of its content words found there
READ_MIN_ORDER = 0.6  # share found in the same order (longest common subsequence)
READ_MAX_WINDOWS = 24  # more candidate windows than this: too common to match
READ_MIN_LAG_S = 3.0  # read at least this long after it was posted
READ_MAX_LAG_S = 1800.0  # and at most this long after
READ_ANSWER_SLACK_S = 300.0  # answered aloud: read at most this long after `responded_at`

VERIFIED_MIN_AGREE = 2  # anchors that agree
VERIFIED_MIN_SHARE = 0.75  # of all anchors
VERIFIED_MAX_PLUSMINUS_S = 15.0  # the offset applied, ± at most this (so a range at most 30 s wide)
ASSUMED_MIN_SHARE = 0.5  # below this share of anchors agreeing: misaligned
ASSUMED_ZERO_SLACK_S = 30.0  # 0 may lie this far outside the agreed range
ASSUMED_MAX_GAP_S = 60.0  # durations agree within max(this, ...
ASSUMED_MAX_GAP_SHARE = 0.02  # ... this share of the session)
DRIFT_TOLERANCE_S = 30.0  # the halves' agreed ranges may lie this far apart
DRIFT_HALF_MAX_WIDTH_S = 60.0  # a half is bounded when its agreed range is at most this wide
DRIFT_HALF_MIN_AGREE = 2  # and that many of its anchors agree

PLACES = 1  # times and offsets are rounded to 0.1 s

# ── Cue phrases, English and French, matched on folded tokens ───────────────

LINK_CUES = (
    "in the chat", "into the chat", "the link", "a link",
    "dans le chat", "le lien", "un lien", "je vous mets", "je vous partage",
)
WAVE_CUES = (
    "tell us in the chat", "let us know in the chat", "say hi", "say hello",
    "where are you joining from", "where you are joining from", "where you're joining from",
    "dites-nous dans le chat", "dites-moi dans le chat", "écrivez dans le chat", "bonjour à tous",
)
# Function words dropped from read-aloud matching, besides the classic
# `COMMON_STOPWORDS` (words under 3 letters are dropped anyway).
EXTRA_STOPWORDS = (
    "about", "all", "any", "could", "how", "many", "more", "much", "there", "were",
    "aux", "ces", "cet", "cette", "comment", "elle", "est-ce", "ils", "leur", "mais", "nous", "par",
    "peut", "quand", "quel", "quelle", "quoi", "ses", "son", "sont", "vos", "votre", "vous",
)

_TOKEN = re.compile(r"[a-z0-9]+")


def fold_tokens(text: str) -> Tuple[str, ...]:
    """`text` as folded tokens: lower-cased, accents removed, split on
    anything that isn't a letter or a digit (`l'intégration` → `l`,
    `integration`; `dites-nous` → `dites`, `nous`). Never cached across
    probes: chat and transcript text must not outlive the build that read
    it (`TokenIndex` keeps its own cache of words, for one probe)."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return tuple(_TOKEN.findall("".join(ch for ch in decomposed if not unicodedata.combining(ch))))


def _phrases(cues: Sequence[str]) -> Tuple[Tuple[str, ...], ...]:
    return tuple(dict.fromkeys(phrase for phrase in (fold_tokens(cue) for cue in cues) if phrase))


_LINK_PHRASES = _phrases(LINK_CUES)
_WAVE_PHRASES = _phrases(WAVE_CUES)
_STOPWORDS: FrozenSet[str] = frozenset(
    token for word in (*COMMON_STOPWORDS, *EXTRA_STOPWORDS) for token in fold_tokens(word)
)


def _is_content(token: str) -> bool:
    return len(token) >= 3 and not token.isdigit() and token not in _STOPWORDS


def content_tokens(text: str) -> List[str]:
    """A message's content words: folded, without stopwords, digits or words
    under 3 letters, each once, in order of first appearance."""
    return list(dict.fromkeys(token for token in fold_tokens(text) if _is_content(token)))


# ── Results ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Anchor:
    """One place where speech and chat meet, and the offset range it allows.

    `e` is the chat side's evidence id (a wave's first burst message), `t`
    its session time; `u` the speech side's utterance ids, `r` its
    recording time (a link's cue start, a wave's cue end, a read's first
    matched word). `agrees` once the sweep has run; `half` is the half of
    the recording it lies in."""

    kind: str
    e: Optional[str]
    u: Tuple[str, ...]
    t: Optional[float]
    r: Optional[float]
    lo: float
    hi: float
    burst: Optional[int] = None
    agrees: bool = False
    half: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        item = {
            "kind": self.kind,
            "e": self.e,
            "u": list(self.u),
            "t": _round(self.t),
            "r": _round(self.r),
            "lo": _round(self.lo),
            "hi": _round(self.hi),
            "agrees": self.agrees,
            "half": self.half,
        }
        if self.kind == WAVE:
            item["burst"] = self.burst
        return item


@dataclass(frozen=True)
class Window:
    """The offset range the most anchors agree on (`count` of them)."""

    lo: float
    hi: float
    count: int
    members: FrozenSet[int]

    @property
    def width(self) -> float:
        return self.hi - self.lo

    @property
    def choice(self) -> float:
        """0 when it lies inside (the recording starts at `started_at`), else the middle."""
        return 0.0 if self.lo <= 0.0 <= self.hi else (self.lo + self.hi) / 2

    @property
    def plusminus(self) -> float:
        return max(self.choice - self.lo, self.hi - self.choice)

    def distance_to_zero(self) -> float:
        return max(self.lo, 0.0, -self.hi)

    def to_json(self) -> Dict[str, Any]:
        return {"offsetS": _round(self.choice), "lo": _round(self.lo), "hi": _round(self.hi), "nAgree": self.count}


@dataclass(frozen=True)
class Alignment:
    """The probe's result. `offset_s` is the offset to apply: the agreed one
    when verified, 0 when assumed, None otherwise. `estimate` is the sweep's
    best range whatever the status (for the study)."""

    status: str
    offset_s: Optional[float]
    plusminus_s: Optional[float]
    source: str
    anchors: Tuple[Anchor, ...]
    n_agree: int
    estimate: Optional[Window]
    durations: Mapping[str, Any]
    halves: Mapping[str, Optional[Window]]
    reasons: Tuple[str, ...]

    @property
    def n_anchors(self) -> int:
        return len(self.anchors)

    def to_json(self) -> Dict[str, Any]:
        """`meta.clock.alignment`: ids and times only."""
        return {
            "version": ALIGNMENT_VERSION,
            "status": self.status,
            "source": self.source,
            "offsetS": _round(self.offset_s),
            "plusminusS": _round(self.plusminus_s),
            "allows": {SECONDS: alignment_allows(self, SECONDS), MINUTES: alignment_allows(self, MINUTES)},
            "nAnchors": self.n_anchors,
            "nAgree": self.n_agree,
            "estimate": self.estimate.to_json() if self.estimate is not None else None,
            "durations": dict(self.durations),
            "drift": {half: (window.to_json() if window is not None else None) for half, window in self.halves.items()},
            "anchors": [anchor.to_json() for anchor in self.anchors],
            "reasons": list(self.reasons),
        }


def alignment_allows(alignment: Any, resolution: str) -> bool:
    """Whether a finding measured at `resolution` may use the transcript on
    the session clock. The one reader of the status:

    - `seconds`: only when the clock is verified;
    - `minutes`: when it is verified or assumed;
    - `audience`: always (chat and questions share Livestorm's clock).

    `alignment` is an `Alignment`, its JSON form (`meta.clock.alignment`)
    or None (nothing but audience). An unknown resolution raises."""
    if resolution not in RESOLUTIONS:
        raise ValueError(f"unknown resolution {resolution!r}")
    if resolution == AUDIENCE:
        return True
    if isinstance(alignment, Alignment):
        status = alignment.status
    elif isinstance(alignment, Mapping):
        status = alignment.get("status")
    else:
        return False
    return status in _ALLOWED[resolution]


def _round(value: Optional[float]) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    rounded = round(float(value), PLACES) + 0.0
    return int(rounded) if rounded.is_integer() else rounded


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ── The token index ─────────────────────────────────────────────────────────


class _Cue(NamedTuple):
    utterance: int  # position in the parsed utterances
    r_start: float  # the first cue word's start
    r_end: float  # the last cue word's end


class TokenIndex:
    """The transcript's words as folded tokens, built once per probe.

    Every utterance's tokens keep their word's own times (an utterance
    without words gives its text at the utterance's times). Content tokens
    are indexed by utterance (`postings`), so a message is compared only
    with the utterances that hold its words."""

    def __init__(self, transcript: ParsedTranscript) -> None:
        utterances = transcript.utterances
        self.uids: List[str] = utterances["uid"].tolist()
        count = len(self.uids)
        u_start = utterances["r_start"].to_numpy(dtype="float64")
        u_end = utterances["r_end"].to_numpy(dtype="float64")
        # Utterances are in id order, by start; those without one come last.
        self.starts = np.where(np.isnan(u_start), np.inf, u_start)
        position = {uid: index for index, uid in enumerate(self.uids)}

        per_utterance: List[List[Tuple[str, float, float]]] = [[] for _ in range(count)]
        folded: Dict[str, Tuple[str, ...]] = {}  # this probe's words, folded once each
        words = transcript.words
        for uid, start, end, word in zip(
            words["uid"].tolist(), words["r_start"].tolist(), words["r_end"].tolist(), words["word"].tolist(), strict=True
        ):
            index = position.get(uid)
            if index is None or not isinstance(word, str):
                continue
            start = start if not math.isnan(start) else u_start[index]
            end = end if not math.isnan(end) else start
            tokens = folded.get(word)
            if tokens is None:
                tokens = folded[word] = fold_tokens(word)
            for token in tokens:
                per_utterance[index].append((token, start, end))
        for index, text in enumerate(utterances["text"].tolist()):
            if not per_utterance[index] and isinstance(text, str) and not math.isnan(u_start[index]):
                end = u_end[index] if not math.isnan(u_end[index]) else u_start[index]
                per_utterance[index] = [(token, u_start[index], end) for token in fold_tokens(text)]

        self.tokens: List[Tuple[Tuple[str, float, float], ...]] = [tuple(items) for items in per_utterance]
        content = {token: _is_content(token) for items in per_utterance for token, _start, _end in items}
        self.content: List[Tuple[Tuple[str, float], ...]] = [
            tuple((token, start) for token, start, _end in items if content[token]) for items in per_utterance
        ]
        postings: Dict[str, List[int]] = {}
        for index, items in enumerate(self.content):
            for token in {token for token, _start in items}:
                postings.setdefault(token, []).append(index)
        self.postings = postings
        self._rows: Dict[str, int] = {}
        self._presence = np.zeros((0, count), dtype=bool)

    def __len__(self) -> int:
        return len(self.uids)

    def cues(self, phrases: Sequence[Tuple[str, ...]]) -> List[Tuple[int, int, int]]:
        """Every occurrence of a phrase inside one utterance: (utterance,
        first token, last token), by position."""
        by_first: Dict[str, List[Tuple[str, ...]]] = {}
        for phrase in phrases:
            by_first.setdefault(phrase[0], []).append(phrase)
        found = []
        for index, items in enumerate(self.tokens):
            tokens = [token for token, _start, _end in items]
            for position, token in enumerate(tokens):
                for phrase in by_first.get(token, ()):
                    if tuple(tokens[position : position + len(phrase)]) == phrase:
                        found.append((index, position, position + len(phrase) - 1))
        return found

    def cue(self, index: int, first: int, last: int) -> _Cue:
        items = self.tokens[index]
        return _Cue(index, items[first][1], max(items[last][2], items[last][1]))

    def prepare(self, vocabulary: Set[str]) -> None:
        """A presence matrix (token × utterance) for the content tokens of
        the messages to match, built once."""
        tokens = sorted(token for token in vocabulary if token in self.postings)
        self._rows = {token: row for row, token in enumerate(tokens)}
        self._presence = np.zeros((len(tokens), len(self)), dtype=bool)
        for row, token in enumerate(tokens):
            self._presence[row, self.postings[token]] = True

    def presence(self, tokens: Sequence[str], first: int, stop: int) -> np.ndarray:
        rows = [self._rows[token] for token in tokens if token in self._rows]
        return self._presence[rows, first:stop]


# ── Durations and bounds ────────────────────────────────────────────────────


def _duration_gap(clock: ClockBase, transcript: Optional[ParsedTranscript]) -> Optional[float]:
    """The session's length − the recording's, or None when either is unknown."""
    audio_s = transcript.audio_s if transcript is not None else None
    return clock.end_s - audio_s if clock.end_s is not None and audio_s is not None else None


def _duration_range(gap: Optional[float]) -> Optional[Tuple[float, float]]:
    """The offsets one continuous recording allows: it starts between the
    session's start and its end minus the recording's length, ±`DURATION_SLACK_S`."""
    return None if gap is None else (min(0.0, gap) - DURATION_SLACK_S, max(0.0, gap) + DURATION_SLACK_S)


def _durations(clock: ClockBase, transcript: Optional[ParsedTranscript]) -> Dict[str, Any]:
    gap = _duration_gap(clock, transcript)
    allowed = _duration_range(gap)
    return {
        "sessionS": _round(clock.end_s),
        "audioS": _round(transcript.audio_s if transcript is not None else None),
        "gapS": _round(gap),
        "endSource": clock.end_source,
        "durationAttrS": _round(clock.duration_attr_s),
        "lo": _round(allowed[0]) if allowed else None,
        "hi": _round(allowed[1]) if allowed else None,
    }


def _search_bound(gap: Optional[float]) -> float:
    return SEARCH_BOUND_S if gap is None else max(SEARCH_BOUND_S, abs(gap) + SEARCH_GAP_MARGIN_S)


def _within(lo: float, hi: float, bound: float) -> bool:
    return lo <= hi and hi >= -bound and lo <= bound


def _pair_cost(lo: float, hi: float) -> Tuple[float, float]:
    """Nearest first: how far the range lies from 0, then how far its middle does."""
    return max(lo, 0.0, -hi), abs((lo + hi) / 2)


# ── Anchors ─────────────────────────────────────────────────────────────────


def _rows(events: Optional[pd.DataFrame]) -> List[Tuple[str, str, float, bool, str, bool, str]]:
    """(eid, kind, t, is_team, text, has_link, livestorm_id) of every chat
    message and question with a session time, in id order."""
    if not isinstance(events, pd.DataFrame) or events.empty:
        return []
    rows = []
    columns = ["eid", "kind", "t", "is_team", "text", "has_link", "livestorm_id"]
    for eid, kind, t, is_team, text, has_link, livestorm_id in events[columns].itertuples(index=False, name=None):
        moment = _finite(t)
        if kind in (CHAT, QUESTION) and moment is not None:
            rows.append((eid, kind, moment, bool(is_team), text if isinstance(text, str) else "", bool(has_link), livestorm_id))
    return rows


def _greedy(candidates: List[Tuple[Tuple[float, float], int, int, Any]]) -> List[Any]:
    """One-to-one pairs, nearest first: each side is used once."""
    used_left: Set[int] = set()
    used_right: Set[int] = set()
    chosen = []
    for _cost, left, right, payload in sorted(candidates, key=lambda item: (item[0], item[1], item[2])):
        if left in used_left or right in used_right:
            continue
        used_left.add(left)
        used_right.add(right)
        chosen.append(payload)
    return chosen


def _inside(span: Tuple[int, int, int], others: Sequence[Tuple[int, int, int]]) -> bool:
    """Whether a cue (utterance, first token, last token) shares a token with one of `others`."""
    utterance, first, last = span
    return any(utterance == other and first <= other_last and other_first <= last for other, other_first, other_last in others)


def _link_anchors(rows, index: TokenIndex, wave_spans, bound: float) -> List[Anchor]:
    """Team messages with a link, each paired with the nearest utterance that
    announces something in the chat; one pair per message and per utterance.
    A cue inside a call to the chat ("tell us in the chat") is a wave's, not a link's."""
    first_cue: Dict[int, _Cue] = {}
    for utterance, first, last in index.cues(_LINK_PHRASES):
        if _inside((utterance, first, last), wave_spans):
            continue
        cue = index.cue(utterance, first, last)
        if utterance not in first_cue or cue.r_start < first_cue[utterance].r_start:
            first_cue[utterance] = cue
    cues = sorted(first_cue.values())
    messages = [(eid, t) for eid, kind, t, is_team, _text, has_link, _id in rows if kind == CHAT and is_team and has_link]
    candidates = []
    for left, (eid, t) in enumerate(messages):
        for right, cue in enumerate(cues):
            gap = t - cue.r_start
            lo, hi = gap - LINK_POSTED_AFTER_S, gap + LINK_POSTED_BEFORE_S
            if _within(lo, hi, bound):
                anchor = Anchor(LINK, eid, (index.uids[cue.utterance],), t, cue.r_start, lo, hi)
                candidates.append((_pair_cost(lo, hi), left, right, anchor))
    return _greedy(candidates)


def _bursts(rows, clock: ClockBase, audience: bool) -> List[Tuple[str, float, int]]:
    """Bursts of audience chat: (first message's eid, its time, messages),
    each at least max(`WAVE_MIN_MESSAGES`, `WAVE_BASELINE_FACTOR` × the
    session's live messages per `WAVE_WINDOW_S`) within `WAVE_WINDOW_S`."""
    messages = sorted((t, eid) for eid, kind, t, is_team, *_rest in rows if kind == CHAT and (audience or not is_team))
    if not messages:
        return []
    times = [t for t, _eid in messages]
    end = clock.end_s
    live = sum(1 for t in times if 0.0 <= t <= (end if end is not None else math.inf))
    baseline = live / (end / WAVE_WINDOW_S) if end else 0.0
    threshold = max(float(WAVE_MIN_MESSAGES), WAVE_BASELINE_FACTOR * baseline)
    bursts = []
    position = 0
    while position < len(times):
        stop = bisect.bisect_right(times, times[position] + WAVE_WINDOW_S)
        if stop - position >= threshold:
            bursts.append((messages[position][1], times[position], stop - position))
            position = stop
        else:
            position += 1
    return bursts


def _wave_anchors(rows, index: TokenIndex, wave_cues, clock: ClockBase, bound: float, audience: bool) -> List[Anchor]:
    """Calls to the chat, each paired with the nearest burst after it; one
    pair per call (an utterance's cues together, ending at the last one) and
    per burst."""
    ends: Dict[int, float] = {}
    for utterance, first, last in wave_cues:
        cue = index.cue(utterance, first, last)
        ends[utterance] = max(ends.get(utterance, -math.inf), cue.r_end)
    calls = sorted(ends.items())
    candidates = []
    for left, (eid, s, size) in enumerate(_bursts(rows, clock, audience)):
        for right, (utterance, r_end) in enumerate(calls):
            lo, hi = s - r_end - WAVE_MAX_LAG_S, s - r_end - WAVE_MIN_LAG_S
            if _within(lo, hi, bound):
                anchor = Anchor(WAVE, eid, (index.uids[utterance],), s, r_end, lo, hi, burst=size)
                candidates.append((_pair_cost(lo, hi), left, right, anchor))
    return _greedy(candidates)


def _lcs(first: Sequence[str], second: Sequence[str]) -> int:
    """The length of the longest common subsequence."""
    previous = [0] * (len(second) + 1)
    for token in first:
        current = [0]
        for position, other in enumerate(second):
            current.append(previous[position] + 1 if token == other else max(previous[position + 1], current[position]))
        previous = current
    return previous[-1]


def _groups(windows: Sequence[Tuple[int, int]]) -> int:
    """How many separate places: windows (first utterance, length) that
    share an utterance are one place."""
    count = 0
    reach = -1
    for first, length in sorted(windows):
        if first > reach:
            count += 1
        reach = max(reach, first + length - 1)
    return count


class _Read(NamedTuple):
    t: float
    eid: str
    first: int
    length: int
    r_read: float
    livestorm_id: str


def _match_read(index: TokenIndex, tokens: List[str], t: float, bound: float) -> Optional[Tuple[int, int, float]]:
    """Where the speakers read a message's words: (first utterance, length,
    recording time of the first matched word), or None when no place, or
    more than one, passes recall and order."""
    count = len(tokens)
    need = math.ceil(READ_MIN_RECALL * count - 1e-9)
    first = int(np.searchsorted(index.starts, t + READ_MIN_LAG_S - bound, side="left"))
    stop = int(np.searchsorted(index.starts, t + READ_MAX_LAG_S + bound, side="right"))
    if stop <= first:
        return None
    sub = index.presence(tokens, first, stop)
    if sub.shape[0] < need:
        return None
    # Windows of 1 to 3 consecutive utterances holding enough of its words,
    # counted before any is listed: a message whose words are everywhere
    # can't be matched uniquely, so it stops here.
    starts: List[np.ndarray] = []
    covered = sub
    for length in range(1, READ_MAX_UTTERANCES + 1):
        if length > 1:
            if covered.shape[1] <= 1:
                break
            covered = covered[:, :-1] | sub[:, length - 1 :]
        starts.append(np.flatnonzero(covered.sum(axis=0) >= need))
        if sum(len(found) for found in starts) > READ_MAX_WINDOWS:
            return None
    windows = [(first + int(start), length) for length, found in enumerate(starts, start=1) for start in found]
    if not windows:
        return None
    wanted = set(tokens)
    passing = []
    for start, length in windows:
        sequence = [(token, at) for u in range(start, start + length) for token, at in index.content[u] if token in wanted]
        found = len({token for token, _at in sequence})
        order = _lcs(tokens, [token for token, _at in sequence]) / count
        if found >= need and order >= READ_MIN_ORDER:
            passing.append((-found, -order, length, start, sequence[0][1]))
    if not passing or _groups([(start, length) for _found, _order, length, start, _r in passing]) != 1:
        return None
    _found, _order, length, start, r_read = min(passing)
    return start, length, r_read


def _read_aloud_anchors(rows, index: TokenIndex, clock: ClockBase, questions_payload: Any, bound: float, audience: bool) -> List[Anchor]:
    """Audience questions and chat messages read aloud. A place two messages
    match counts once, for the earlier message (its bound is the weaker).

    Team chat is never read aloud, even on an internal session (`audience`):
    the one typing may be the one speaking, and type the words after saying
    them, which breaks "chat, then speech". There, a team member's question
    can be: a question is asked before it is read."""
    candidates = []
    for eid, kind, t, is_team, text, _link, livestorm_id in rows:
        eligible = not is_team or (audience and kind == QUESTION)
        tokens = content_tokens(text) if eligible else []
        if READ_MIN_WORDS <= len(tokens) <= READ_MAX_WORDS:
            candidates.append((eid, t, livestorm_id, tokens))
    if not candidates:
        return []
    index.prepare({token for *_rest, tokens in candidates for token in tokens})
    reads = []
    for eid, t, livestorm_id, tokens in candidates:
        match = _match_read(index, tokens, t, bound)
        if match is not None:
            reads.append(_Read(t, eid, match[0], match[1], match[2], livestorm_id))

    answered_aloud = {}
    if clock.t0_unix is not None:
        for item in question_items(questions_payload):
            responded = parse_time(item.responded_at)
            if item.answered_orally and item.livestorm_id and responded is not None:
                answered_aloud[item.livestorm_id] = responded - clock.t0_unix
    question_ids = {eid for eid, kind, *_rest in rows if kind == QUESTION}

    claimed: Set[int] = set()
    anchors = []
    for read in sorted(reads):
        utterances = set(range(read.first, read.first + read.length))
        if utterances & claimed:
            continue
        claimed |= utterances
        lo = read.t - read.r_read + READ_MIN_LAG_S
        hi = read.t - read.r_read + READ_MAX_LAG_S
        responded = answered_aloud.get(read.livestorm_id) if read.eid in question_ids else None
        if responded is not None:
            hi = min(hi, responded - read.r_read + READ_ANSWER_SLACK_S)
        if _within(lo, hi, bound):
            uids = tuple(index.uids[u] for u in sorted(utterances))
            anchors.append(Anchor(READ_ALOUD, read.eid, uids, read.t, read.r_read, lo, hi))
    return anchors


def _media_anchor(transcript: ParsedTranscript, clock: ClockBase, bound: float) -> List[Anchor]:
    """The recording file's `creation_time` as its start. Off (`MEDIA_TIME_ANCHOR`)."""
    if not MEDIA_TIME_ANCHOR:
        return []
    created = parse_time(transcript.media_creation_time)
    if created is None or clock.t0_unix is None:
        return []
    start = created - clock.t0_unix
    lo, hi = start - MEDIA_TIME_SLACK_S, start + MEDIA_TIME_SLACK_S
    return [Anchor(MEDIA_TIME, None, (), start, 0.0, lo, hi)] if _within(lo, hi, bound) else []


def _distinct(anchors: Sequence[Anchor]) -> List[Anchor]:
    """One message and one utterance feed at most one anchor, in the order
    given (links, then waves, then reads aloud): an anchor whose message or
    any of whose utterances an earlier one used is dropped. Otherwise one
    moment (a link posted and read out) would confirm itself."""
    used_e: Set[str] = set()
    used_u: Set[str] = set()
    kept = []
    for anchor in anchors:
        if (anchor.e is not None and anchor.e in used_e) or used_u.intersection(anchor.u):
            continue
        kept.append(anchor)
        if anchor.e is not None:
            used_e.add(anchor.e)
        used_u.update(anchor.u)
    return kept


# ── The sweep ───────────────────────────────────────────────────────────────


def sweep(ranges: Sequence[Tuple[float, float]], domain: Tuple[float, float]) -> Optional[Window]:
    """The offset range covered by the most ranges, inside `domain` (ranges
    are clipped to it; one that misses it covers nothing). Among ranges
    tied for the most, the one holding 0, else the nearest to 0. None
    when no range reaches the domain."""
    events = []
    for position, (lo, hi) in enumerate(ranges):
        low, high = max(lo, domain[0]), min(hi, domain[1])
        if low <= high:
            events.append((low, 0, position))  # starts before ends at one point: closed ranges
            events.append((high, 1, position))
    events.sort()
    best = 0
    found: List[Window] = []
    active: Set[int] = set()
    opened: Optional[float] = None
    for x, is_end, position in events:
        if not is_end:
            active.add(position)
            if len(active) > best:
                best, found, opened = len(active), [], x
            elif len(active) == best and opened is None:
                opened = x
        else:
            if len(active) == best and opened is not None:
                found.append(Window(opened, x, best, frozenset(active)))
                opened = None
            active.discard(position)
    if not found:
        return None
    return min(found, key=lambda window: (window.distance_to_zero(), abs((window.lo + window.hi) / 2), window.lo))


def _directions(anchors: Sequence[Anchor], members: FrozenSet[int]) -> FrozenSet[str]:
    return frozenset(side for position in members for side in BOUNDS[anchors[position].kind])


# ── The probe ───────────────────────────────────────────────────────────────


def _unavailable(durations: Mapping[str, Any], reasons: List[str]) -> Alignment:
    return Alignment(
        status=UNAVAILABLE, offset_s=None, plusminus_s=None, source=SOURCE_ANCHORS, anchors=(), n_agree=0,
        estimate=None, durations=durations, halves={EARLY: None, LATE: None}, reasons=tuple(reasons),
    )


def _midpoint(transcript: ParsedTranscript) -> Optional[float]:
    """Where the recording's halves meet: half its length, else half the
    last utterance's end; None without either."""
    if transcript.audio_s:
        return transcript.audio_s / 2
    ends = pd.concat([transcript.utterances["r_end"], transcript.utterances["r_start"]]).dropna()
    return float(ends.max()) / 2 if len(ends) and ends.max() > 0 else None


def _halves(anchors: Sequence[Anchor], midpoint: Optional[float], bound: float) -> Dict[str, Optional[Window]]:
    """Each half's agreed range, when bounded: at least
    `DRIFT_HALF_MIN_AGREE` anchors agree on at most `DRIFT_HALF_MAX_WIDTH_S`.
    The duration constraint is left out: it assumes one continuous recording."""
    halves: Dict[str, Optional[Window]] = {}
    for half in (EARLY, LATE):
        ranges = [(anchor.lo, anchor.hi) for anchor in anchors if anchor.half == half]
        window = sweep(ranges, (-bound, bound)) if midpoint is not None else None
        bounded = window is not None and window.count >= DRIFT_HALF_MIN_AGREE and window.width <= DRIFT_HALF_MAX_WIDTH_S
        halves[half] = window if bounded else None
    return halves


def _drift(halves: Mapping[str, Optional[Window]]) -> bool:
    """Both halves bounded, and their agreed ranges more than
    `DRIFT_TOLERANCE_S` apart: no offset change of that much or less fits
    both. Ranges, not their chosen offsets, which snap to 0 when it lies inside."""
    early, late = halves[EARLY], halves[LATE]
    if early is None or late is None:
        return False
    return max(early.lo, late.lo) - min(early.hi, late.hi) > DRIFT_TOLERANCE_S


def probe_alignment(
    transcript: Optional[ParsedTranscript],
    events: Optional[pd.DataFrame],
    clock: ClockBase,
    *,
    questions_payload: Any = None,
    internal: bool = False,
) -> Alignment:
    """Where the recording sits on the session clock (module docstring).

    - `transcript`: `transcript.parse_transcript(...)`, parsed once by
      `inputs.load_record_inputs`; None gives `unavailable`;
    - `events`: `evidence.audience_events(...)`, built once there too;
    - `clock`: the session clock; without `started_at`, `unavailable`;
    - `questions_payload`: read for the oral flag and `responded_at`;
    - `internal`: on an internal session everyone is audience, so any
      message can make a burst and any question can be read aloud (team
      chat never is: `_read_aloud_anchors`).
    """
    durations = _durations(clock, transcript)
    missing = ([] if transcript is not None else ["no_transcript"]) + ([] if clock.has_clock else ["no_clock"])
    if missing:
        return _unavailable(durations, missing)

    gap = _duration_gap(clock, transcript)
    constraint = _duration_range(gap)
    bound = _search_bound(gap)
    rows = _rows(events)
    index = TokenIndex(transcript)
    wave_cues = index.cues(_WAVE_PHRASES)
    anchors = _distinct([
        *_link_anchors(rows, index, wave_cues, bound),
        *_wave_anchors(rows, index, wave_cues, clock, bound, internal),
        *_read_aloud_anchors(rows, index, clock, questions_payload, bound, internal),
        *_media_anchor(transcript, clock, bound),
    ])
    midpoint = _midpoint(transcript)
    anchors = [
        replace(anchor, half=None if anchor.kind == MEDIA_TIME or midpoint is None else (EARLY if anchor.r < midpoint else LATE))
        for anchor in sorted(anchors, key=lambda item: (item.r if item.r is not None else -math.inf, item.kind, item.e or ""))
    ]

    domain = constraint if constraint is not None else (-bound, bound)
    best = sweep([(anchor.lo, anchor.hi) for anchor in anchors], domain)
    members = best.members if best is not None else frozenset()
    anchors = [replace(anchor, agrees=position in members) for position, anchor in enumerate(anchors)]
    n_anchors, n_agree = len(anchors), len(members)

    halves = _halves(anchors, midpoint, bound)
    drift = _drift(halves)

    unverified: List[str] = []
    if n_anchors == 0:
        unverified.append("no_anchors")
    else:
        if n_agree < VERIFIED_MIN_AGREE:
            unverified.append("few_anchors")
        if n_agree / n_anchors < VERIFIED_MIN_SHARE:
            unverified.append("weak_agreement")
        if best is None or best.plusminus > VERIFIED_MAX_PLUSMINUS_S:
            unverified.append("wide_range")
        if _directions(anchors, members) != {LOWER, UPPER}:
            unverified.append("one_direction")
    if drift:
        unverified.append("drift")

    common = dict(source=SOURCE_ANCHORS, anchors=tuple(anchors), n_agree=n_agree, estimate=best, durations=durations, halves=halves)
    if not unverified:
        return Alignment(status=VERIFIED, offset_s=best.choice, plusminus_s=best.plusminus, reasons=(), **common)

    misaligned: List[str] = []
    if gap is None:
        misaligned.append("duration_unknown")
    elif abs(gap) > max(ASSUMED_MAX_GAP_S, ASSUMED_MAX_GAP_SHARE * (clock.end_s or 0.0)):
        misaligned.append("duration_gap")
    if n_anchors and n_agree / n_anchors < ASSUMED_MIN_SHARE:
        misaligned.append("anchors_disagree")
    if best is not None and best.distance_to_zero() > ASSUMED_ZERO_SLACK_S:
        misaligned.append("offset_not_zero")
    if drift:
        misaligned.append("drift")
    if misaligned:
        return Alignment(status=MISALIGNED, offset_s=None, plusminus_s=None, reasons=tuple(misaligned), **common)
    return Alignment(status=ASSUMED, offset_s=0.0, plusminus_s=None, reasons=tuple(unverified), **common)
