"""The understanding job's input and its checks (v1, plan 1e).

One model call reads a session written out as a **dossier** and returns its
chapters, the passages that answer the audience's questions, the prompts to
the room, the figures said on stage and the questions that repeat each other
(`ai/understanding.py`). This module is the pure part around that call:

- `build_dossier(inputs)`: the session as text, by id. Utterances (`U0001`)
  with their speaker *key* (never a name the host saved), their times and
  their text; the audience's questions (`Q001`, `C0001`, from
  `question_desk.audience_questions`) with their Livestorm status, time and
  text. No name, email, company, people id or `P-` ref. Times are on the
  session clock only when the alignment allows minutes
  (`alignment_allows(…, MINUTES)`); otherwise utterances carry recording
  times and questions carry none, and the dossier says so. Built with
  `ai.dossier`'s helpers, so the same session gives the same bytes.
- `check_understanding(answer, inputs, questions)`: what the model said,
  kept only where code can check it. Every id must exist; a quote or a span
  must be in its utterances, word for word (case, punctuation and spacing
  aside); a located answer must come after its question on the session
  clock, so without one no answer is kept; a figure must hold a number.
  Figures get flags for the host ("Before you share"): the same metric said
  with two values, a confusable acronym (NPS/NRR, ARR/MRR, CSAT/CSM) in
  words the transcript is unsure of. What fails a check is dropped and
  counted in `coverage`, never shown.

Pure: no database, network or model call.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from livestorm_app.ai.dossier import Dossier, fixed_number, make_dossier
from livestorm_app.record.alignment import MINUTES, alignment_allows
from livestorm_app.record.question_desk import audience_questions

# Bump when the dossier, the checks or what is stored changes: a stored
# result of another version is stale and the next analysis reads again.
UNDERSTANDING_VERSION = 1

CHAPTER_KINDS = ("opening", "content", "demo", "qa", "close", "other")
MAX_TITLE_CHARS = 80
MAX_QUESTION_CHARS = 600
MAX_ANSWER_UTTERANCES = 6
# Words below this mean confidence are ones the transcript is unsure of.
LOW_CONFIDENCE = 0.8
# Acronyms a listener (or a transcript) easily mixes up.
CONFUSABLE = {"NPS": "NRR", "NRR": "NPS", "ARR": "MRR", "MRR": "ARR", "CSAT": "CSM", "CSM": "CSAT"}

# Located answers are kept only for questions Livestorm doesn't show as
# answered in writing: an open one, or one marked answered aloud (the passage
# says where).
LOCATABLE = ("open", "aloud")

_WHITESPACE = re.compile(r"\s+")
_NUMBER = re.compile(r"\d+(?:[   .,]\d{3})*(?:[.,]\d+)?")


def _one_line(value: Any, limit: Optional[int] = None) -> str:
    text = _WHITESPACE.sub(" ", str(value or "")).strip()
    if limit is not None and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clock(inputs: Any) -> Optional[float]:
    """The offset that puts speech on the session clock, or None when the
    alignment doesn't allow findings to the minute."""
    alignment = getattr(inputs, "alignment", None)
    if alignment is None or not alignment_allows(alignment, MINUTES):
        return None
    return _finite(getattr(alignment, "offset_s", None))


def _utterances(inputs: Any) -> List[Dict[str, Any]]:
    """The transcript's utterances in id order: `{id, speaker, r, rEnd, t,
    text, conf}`, `t` on the session clock or None."""
    transcript = getattr(inputs, "transcript", None)
    frame = getattr(transcript, "utterances", None)
    if frame is None or frame.empty:
        return []
    offset = _clock(inputs)
    rows = []
    for row in frame.sort_values(["r_start", "order"]).to_dict("records"):
        text = _one_line(row.get("text"))
        if not text:
            continue
        r = _finite(row.get("r_start"))
        rows.append(
            {
                "id": str(row["uid"]),
                "speaker": f"S{row['speaker_key']}" if row.get("speaker_key") is not None else "S?",
                "r": r,
                "rEnd": _finite(row.get("r_end")),
                "t": r + offset if r is not None and offset is not None else None,
                "text": text,
                "conf": _finite(row.get("conf_mean")),
            }
        )
    return rows


def _time(value: Optional[float]) -> str:
    return fixed_number(value, 1) if value is not None else "-"


def build_dossier(inputs: Any) -> Tuple[Dossier, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """`(dossier, utterances, questions)`: the dossier, and the rows it was
    written from, which `check_understanding` checks the answer against."""
    utterances = _utterances(inputs)
    on_clock = _clock(inputs) is not None
    questions = audience_questions(inputs)
    transcript = getattr(inputs, "transcript", None)
    languages = list(getattr(transcript, "languages", None) or [])
    lines = [
        "# Session dossier",
        f"version: {UNDERSTANDING_VERSION}",
        f"language: {languages[0] if languages else 'unknown'}",
        f"times: {'session clock (seconds from the start of the session)' if on_clock else 'recording (seconds from the start of the recording; questions have no time)'}",
        "",
        "## Utterances",
        "id\tspeaker\tstart\tend\ttext",
    ]
    for u in utterances:
        start = u["t"] if on_clock else u["r"]
        length = u["rEnd"] - u["r"] if u["rEnd"] is not None and u["r"] is not None else None
        end = start + length if start is not None and length is not None else None
        lines.append(f"{u['id']}\t{u['speaker']}\t{_time(start)}\t{_time(end)}\t{u['text']}")
    lines += ["", "## Audience questions", "id\tsource\tstatus\ttime\ttext"]
    for q in questions:
        when = _time(_finite(q["t"])) if on_clock else "-"
        lines.append(f"{q['id']}\t{q['source']}\t{q['answered_as']}\t{when}\t{_one_line(q['text'], MAX_QUESTION_CHARS)}")
    dossier = make_dossier("\n".join(lines), record_version=f"understanding-{UNDERSTANDING_VERSION}")
    return dossier, utterances, questions


# ── The checks ──────────────────────────────────────────────────────────────


def _plain(text: Any) -> str:
    """Text compared word for word: NFKC, case-folded, punctuation dropped,
    spaces collapsed."""
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    value = "".join(ch if ch.isalnum() else " " for ch in value)
    return _WHITESPACE.sub(" ", value).strip()


def _verbatim(quote: Any, texts: Sequence[str]) -> bool:
    needle = _plain(quote)
    return bool(needle) and needle in _plain(" ".join(texts))


def parse_number(text: Any) -> Optional[float]:
    """The first number in `text`, read in English or French: "1,200.5",
    "1 200,5", "12.5", "12,5", "40". None without digits."""
    match = _NUMBER.search(unicodedata.normalize("NFKC", str(text or "")))
    if not match:
        return None
    raw = re.sub(r"[   ]", "", match.group(0))
    if "," in raw and "." in raw:
        decimal = "," if raw.rfind(",") > raw.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        raw = raw.replace(thousands, "").replace(decimal, ".")
    elif "," in raw:
        head, _, tail = raw.rpartition(",")
        raw = raw.replace(",", "") if len(tail) == 3 and head else raw.replace(",", ".")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _ids(values: Any) -> List[str]:
    return [str(v).strip() for v in values or [] if str(v).strip()]


def _chapters(items: Sequence[Mapping[str, Any]], by_id: Mapping[str, Dict[str, Any]], order: Mapping[str, int]) -> List[Dict[str, Any]]:
    kept: List[Dict[str, Any]] = []
    for item in items:
        first, last = str(item.get("first_utterance") or ""), str(item.get("last_utterance") or "")
        title = _one_line(item.get("title"), MAX_TITLE_CHARS)
        kind = item.get("kind") if item.get("kind") in CHAPTER_KINDS else "other"
        if first not in by_id or last not in by_id or order[first] > order[last] or not title:
            continue
        kept.append({"title": title, "kind": kind, "first": first, "last": last, "t": by_id[first]["t"], "tEnd": by_id[last]["t"]})
    kept.sort(key=lambda c: order[c["first"]])
    no_overlap: List[Dict[str, Any]] = []
    for chapter in kept:
        if no_overlap and order[chapter["first"]] <= order[no_overlap[-1]["last"]]:
            continue
        no_overlap.append(chapter)
    return no_overlap


def _answers(
    items: Sequence[Mapping[str, Any]],
    by_id: Mapping[str, Dict[str, Any]],
    questions: Mapping[str, Dict[str, Any]],
    on_clock: bool,
) -> List[Dict[str, Any]]:
    if not on_clock:
        return []  # "after the question" can't be checked: nothing is guessed
    kept: Dict[str, Dict[str, Any]] = {}
    for item in items:
        question = questions.get(str(item.get("question_id") or ""))
        uids = _ids(item.get("utterance_ids"))[:MAX_ANSWER_UTTERANCES]
        if question is None or question["answered_as"] not in LOCATABLE or question["id"] in kept:
            continue
        if not uids or any(uid not in by_id for uid in uids):
            continue
        asked = _finite(question.get("t"))
        start = by_id[uids[0]]["t"]
        if asked is None or start is None or start < asked:
            continue
        if not _verbatim(item.get("quote"), [by_id[uid]["text"] for uid in uids]):
            continue
        kept[question["id"]] = {"question": question["id"], "utterances": uids, "t": start, "quote": _one_line(item.get("quote"), 400)}
    return sorted(kept.values(), key=lambda a: a["t"])


def _prompts(items: Sequence[Mapping[str, Any]], by_id: Mapping[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    kept: Dict[str, Dict[str, Any]] = {}
    for item in items:
        uid = str(item.get("utterance_id") or "")
        if uid in by_id and uid not in kept and _verbatim(item.get("quote"), [by_id[uid]["text"]]):
            kept[uid] = {"utterance": uid, "t": by_id[uid]["t"], "quote": _one_line(item.get("quote"), 200)}
    return list(kept.values())


def _metric_key(value: Any) -> str:
    return _plain(value)


def _figures(items: Sequence[Mapping[str, Any]], by_id: Mapping[str, Dict[str, Any]], order: Mapping[str, int]) -> List[Dict[str, Any]]:
    kept: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()
    for item in items:
        uid = str(item.get("utterance_id") or "")
        span = _one_line(item.get("span"), 200)
        if uid not in by_id or not _verbatim(span, [by_id[uid]["text"]]):
            continue
        number = parse_number(item.get("value")) if parse_number(item.get("value")) is not None else parse_number(span)
        if number is None or (uid, _plain(span)) in seen:
            continue
        seen.add((uid, _plain(span)))
        kept.append(
            {
                "utterance": uid,
                "t": by_id[uid]["t"],
                "span": span,
                "value": _one_line(item.get("value"), 40),
                "number": number,
                "unit": _one_line(item.get("unit"), 20) or None,
                "metric": _one_line(item.get("metric"), 60) or None,
                "flags": [],
            }
        )
    kept.sort(key=lambda f: order[f["utterance"]])
    # The same metric said with two values.
    by_metric: Dict[str, Set[Tuple[float, str]]] = {}
    for figure in kept:
        if figure["metric"]:
            by_metric.setdefault(_metric_key(figure["metric"]), set()).add((figure["number"], (figure["unit"] or "").casefold()))
    for figure in kept:
        if figure["metric"] and len(by_metric[_metric_key(figure["metric"])]) > 1:
            figure["flags"].append("two_values")
    # A confusable acronym in words the transcript is unsure of.
    for figure in kept:
        conf = by_id[figure["utterance"]]["conf"]
        words = set(re.findall(r"[A-Za-z]+", f"{figure['span']} {figure['metric'] or ''}"))
        acronyms = sorted(word.upper() for word in words if word.upper() in CONFUSABLE and word.isupper())
        if acronyms and (conf is None or conf < LOW_CONFIDENCE):
            figure["flags"].append("acronym")
            figure["confusable"] = {a: CONFUSABLE[a] for a in acronyms}
    for number, figure in enumerate(kept, start=1):
        figure["id"] = f"F{number:02d}"
    return kept


def _groups(items: Sequence[Mapping[str, Any]], questions: Mapping[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    kept, used = [], set()
    for item in items:
        ids = [qid for qid in dict.fromkeys(_ids(item.get("question_ids"))) if qid in questions and qid not in used]
        label = _one_line(item.get("label"), MAX_TITLE_CHARS)
        if len(ids) < 2 or not label:
            continue
        used.update(ids)
        kept.append({"label": label, "questions": ids})
    return kept


KEYS = ("chapters", "answers", "prompts", "figures", "groups")


def check_understanding(
    answer: Mapping[str, Any],
    utterances: Sequence[Dict[str, Any]],
    questions: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """What is stored: `{version, onClock, chapters, answers, prompts,
    figures, groups, coverage}`, each item checked against the dossier's
    rows (`build_dossier`). `coverage` counts what the model sent and what
    was kept, per key."""
    by_id = {u["id"]: u for u in utterances}
    order = {u["id"]: index for index, u in enumerate(utterances)}
    by_question = {q["id"]: q for q in questions}
    on_clock = any(u["t"] is not None for u in utterances)
    result = {
        "chapters": _chapters(answer.get("chapters") or [], by_id, order),
        "answers": _answers(answer.get("answers") or [], by_id, by_question, on_clock),
        "prompts": _prompts(answer.get("prompts") or [], by_id),
        "figures": _figures(answer.get("figures") or [], by_id, order),
        "groups": _groups(answer.get("groups") or [], by_question),
    }
    coverage = {
        "received": {key: len(answer.get(key) or []) for key in KEYS},
        "kept": {key: len(result[key]) for key in KEYS},
        "utterances": len(utterances),
        "questions": len(questions),
    }
    return {"version": UNDERSTANDING_VERSION, "onClock": on_clock, **result, "coverage": coverage}


# ── Reading it back ─────────────────────────────────────────────────────────

READY = "ready"
STALE = "stale"
NONE = "none"


def located_answers(stored: Optional[Mapping[str, Any]], inputs: Any) -> Tuple[Dict[str, Dict[str, Any]], str]:
    """The stored understanding's located answers, by question id, for the
    question desk: `({question_id: {t, quote, utterances}}, state)`.

    Question and utterance ids are the dossier's, which shift when the
    session is fetched again or its chat labelled again, so the answers are
    used only while the stored result was read from this session's dossier
    now (same hash, same version): state `ready`. Otherwise `stale` (read
    from another dossier) or `none` (never read, or no transcript), with no
    answers."""
    if not stored:
        return {}, NONE
    dossier, utterances, _ = build_dossier(inputs)
    if not utterances:
        return {}, NONE
    if stored.get("version") != UNDERSTANDING_VERSION or stored.get("dossier_hash") != dossier.sha256:
        return {}, STALE
    result = stored.get("result") if isinstance(stored.get("result"), Mapping) else {}
    answers: Dict[str, Dict[str, Any]] = {}
    for item in result.get("answers") or []:
        if not isinstance(item, Mapping):
            continue
        question, t, quote = str(item.get("question") or ""), _finite(item.get("t")), _one_line(item.get("quote"))
        if question and t is not None and quote and question not in answers:
            answers[question] = {"t": t, "quote": quote, "utterances": _ids(item.get("utterances"))}
    return answers, READY
