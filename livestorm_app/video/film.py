"""Surprise me's video as a HyperFrames project: one HTML page whose every
visible change is a GSAP tween on one paused timeline at an absolute time,
built from the saved timeline (`ai/surprise_timing.py`) and shot list
(`ai/surprise_director.py`), plus the list of sound effects the mixer
places (`video/mix.py`). The design is the one the owner approved in the
November 2026 rehearsals: a fixed intro (the IQ mark, "Surprise me!", "The
session, unfiltered.") and outro ("See you next session.", then the end
card), kinetic type on every clause, the held beat before a punchline
frozen (grey, a slow push-in), then the punchline's picture and caption
with a low "dun" (owner's brief of 2026-11-12; drawn by `video/punch.py`,
not here), subtle CC0 effects, no music.

The video is parts joined end to end by the renderer (`video/render.py`):
the intro and the outro (`build_bumper`) hold nothing from a show, so each
is rendered once and stored (`surprise_bumpers`); a show's middle, from the
intro's end to the end card, is its kinetic parts (Chrome) and its
punchline windows (Pillow and ffmpeg) in turn (`layout`). Chrome renders
every kinetic part in one page (`build_film`), back to back, each starting
on a multiple of `KEYFRAME_FRAMES` (where the renderer's encode puts a
keyframe), so the renderer cuts them out by copy; the few frames between
two parts are still and thrown away. The progress bar runs over the middle
only (the windows draw it too) and the background is one still picture
(`templates/background.png`), so the joins can't be seen. The sound is
placed on the whole video's clock (`spans`): the effects `build_film`
returns include the intro's, the outro's and each punchline's "dun".

No GPU on the render worker: every frame is drawn by the CPU. The blurred,
turning glow behind everything cost two thirds of each frame, so the
background is a still picture; the middle's moves are transform and
opacity tweens only (a CSS blur is redrawn on every frame). The intro and
the outro keep their blurs: they are drawn once.

Pure: no database, no network, no subprocess. Text from the session (the
clauses' words, the room's lines) is HTML-escaped; nothing from the session
reaches the page's script but times and numbers.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from livestorm_app.video.keys import FPS, HEIGHT, KEYFRAME_FRAMES, WIDTH

W, H = 1920, 1080  # the frame the design is drawn in; the page scales it to 1280x720
NAVY, NAVY2, TEAL, INK = "#0A1430", "#0F1E46", "#2EE6C9", "#F4F7FF"
SHAKE = [(14, -9), (-12, 7), (9, 10), (-7, -6), (5, 4), (-3, 2), (0, 0)]
TAGLINE = "The session, unfiltered."
SIGNOFF = "See you next session."
SIGNOFF_S = 2.3  # the sign-off's time before the end card
# The intro and outro jingle (`templates/audio/`, sources in its SOURCES.md).
JINGLE = "bass-meant-jazz"
JINGLE_GAIN = 0.9
TYPES_DRAWN = ("kinetic", "count", "ring", "pulse", "emoji", "chat", "split", "stamp")
DUN_GAIN = 0.55  # the low "dun" as a punchline's picture lands


def jingle_files(name: str = JINGLE) -> Tuple[str, str]:
    return f"jingle-{name}-in.flac", f"jingle-{name}-out.flac"


def frames(seconds: float) -> int:
    """`seconds` as a whole number of frames (at least one)."""
    return max(1, int(round(float(seconds) * FPS)))


def spans(timeline: Mapping[str, Any]) -> Dict[str, float]:
    """The three parts' lengths on whole frames, and where they meet:
    `{intro, middle, outro, join, total}` in seconds. The middle runs from
    the timeline's `intro_s` to its `end_card`; `join` is where the outro
    starts in the joined video (the end card within half a frame)."""
    intro_s = float(timeline["intro_s"])
    end_card = float(timeline["end_card"])
    outro_raw = float(timeline.get("outro_s") or (float(timeline["total"]) - end_card))
    intro = frames(intro_s) / FPS
    middle = frames(end_card - intro_s) / FPS
    outro = frames(outro_raw) / FPS
    return {"intro": intro, "middle": middle, "outro": outro, "join": intro + middle, "total": intro + middle + outro}


def layout(timeline: Mapping[str, Any]) -> Dict[str, Any]:
    """The middle's parts on whole frames: `{middle_frames, page_frames,
    segments: [{kind, n?, start, end, f0, f1, frames, c0?}]}`. `f0`, `f1`:
    the part's frames in the middle, end to end from 0 to `middle_frames`;
    `c0` (kinetic parts): its first frame on the Chrome page, a multiple of
    `KEYFRAME_FRAMES`; `page_frames`: the page's length."""
    intro_s = float(timeline["intro_s"])
    middle_frames = round(spans(timeline)["middle"] * FPS)
    raw = list(timeline.get("segments") or []) or [
        {"kind": "kinetic", "start": intro_s, "end": float(timeline["end_card"])}
    ]
    segments: List[Dict[str, Any]] = []
    for index, seg in enumerate(raw):
        f0 = 0 if index == 0 else round((float(seg["start"]) - intro_s) * FPS)
        f1 = middle_frames if index == len(raw) - 1 else round((float(seg["end"]) - intro_s) * FPS)
        f0, f1 = max(0, min(f0, middle_frames)), max(0, min(f1, middle_frames))
        if segments:
            f0 = segments[-1]["f1"]
        if f1 <= f0:
            continue
        entry = {"kind": seg["kind"], "start": float(seg["start"]), "end": float(seg["end"]), "f0": f0, "f1": f1, "frames": f1 - f0}
        if seg["kind"] == "punch":
            entry["n"] = int(seg["n"])
        segments.append(entry)
    page = 0
    page_frames = 0
    for entry in segments:
        if entry["kind"] == "kinetic":
            entry["c0"] = page
            page_frames = page + entry["frames"]
            page = -(-page_frames // KEYFRAME_FRAMES) * KEYFRAME_FRAMES
    return {"middle_frames": middle_frames, "page_frames": page_frames, "segments": segments}


def esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def _js(value: Any) -> str:
    """JSON for the page's script, safe inside a <script> element."""
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")


def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(word or "").lower())


def word_times(words: Sequence[str], spoken: Sequence[Mapping[str, Any]], start: float, end: float) -> List[float]:
    """The time each on-screen word is heard (its whisper stamp), else spread
    evenly from the clause's start; never before the clause or at its very end."""
    out: List[Optional[float]] = []
    j = 0
    for word in words:
        hit = None
        target = norm(word)
        for k in range(j, len(spoken)):
            heard = norm(spoken[k].get("w"))
            if heard == target or (len(target) > 3 and heard.startswith(target[:4])):
                hit, j = float(spoken[k]["t"]), k + 1
                break
        out.append(hit)
    span = max(0.3, min(end - start - 0.25, 1.6))
    times = [t if t is not None else start + span * n / max(1, len(words)) for n, t in enumerate(out)]
    times = [min(max(start, t - 0.04), max(start, end - 0.15)) for t in times]
    for n in range(1, len(times)):
        times[n] = max(times[n], times[n - 1])
    return times


def fit(chars: int, big: bool = False) -> int:
    base = 210 if big else 170
    one = 1650 / (0.6 * max(chars, 1))
    if one >= 90:
        return int(min(base, one))
    return int(min(base, 2 * 1650 / (0.6 * chars)))


def parse_number(text: str) -> Optional[Dict[str, Any]]:
    match = re.match(r"^(\D*?)(\d+):(\d\d)(\D*)$", text)
    if match:
        return {"kind": "clock", "pre": match.group(1), "to": int(match.group(2)) * 60 + int(match.group(3)), "post": match.group(4)}
    match = re.match(r"^(\D*?)([\d,]*\.?\d+)(\D*)$", text)
    if not match:
        return None
    raw = match.group(2).replace(",", "")
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    return {"kind": "num", "pre": match.group(1), "to": float(raw), "dec": decimals, "post": match.group(3),
            "comma": "," in match.group(2)}


class _Page:
    """A page's tweens. Times are given on the whole video's clock and
    written on the page's own: `offset` earlier, or, with `parts` (`[(start
    on the video's clock, start on the page, length)]`, in order), placed in
    the part the time falls in (the last one starting at or before it, to
    within half a frame: the parts start on whole frames), never before its
    start nor past its end."""

    def __init__(self, offset: float = 0.0, parts: Sequence[Tuple[float, float, float]] = ()) -> None:
        self.offset = float(offset)
        self.parts = [(float(a), float(b), float(c)) for a, b, c in parts]
        self.js: List[str] = []

    def page_time(self, t: float) -> float:
        if not self.parts:
            return t - self.offset
        video, page, length = self.parts[0]
        half = 0.5 / FPS
        for start, on_page, span in self.parts:
            if start <= t + half + 1e-6:
                video, page, length = start, on_page, span
        into = t - video
        return page + (0.0 if into < half else min(into, length))

    def at(self, t: float) -> str:
        return f"{self.page_time(t):.3f}"

    def tw(self, sel: str, frm: Mapping[str, Any], to: Mapping[str, Any], at: float) -> None:
        self.js.append(f"tl.fromTo({_js(sel)},{_js(frm)},{_js(to)},{self.at(at)});")

    def set(self, sel: str, to: Mapping[str, Any], at: float) -> None:
        self.js.append(f"tl.set({_js(sel)},{_js(to)},{self.at(at)});")

    def reveal_words(self, pid: str, times: Sequence[float], style: str = "pop") -> None:
        """Each word in at its time: `rise` (up from below), `slam` (from
        big), `pop` (from a little big), `blur` (out of a blur: the
        bumpers only, drawn once)."""
        for n, t in enumerate(times):
            sel = f"#{pid}-w{n}"
            if style == "slam":
                self.tw(sel, {"opacity": 0, "scale": 1.9}, {"opacity": 1, "scale": 1, "duration": 0.2, "ease": "power4.out"}, t)
            elif style == "rise":
                self.tw(sel, {"opacity": 0, "y": 40}, {"opacity": 1, "y": 0, "duration": 0.22, "ease": "expo.out"}, t)
            elif style == "blur":
                self.tw(sel, {"opacity": 0, "filter": "blur(8px)"},
                        {"opacity": 1, "filter": "blur(0px)", "duration": 0.14, "ease": "power4.out"}, t)
            else:
                self.tw(sel, {"opacity": 0, "scale": 1.18}, {"opacity": 1, "scale": 1, "duration": 0.16, "ease": "power4.out"}, t)

    def page(self, css: str, duration: float, body: str) -> str:
        return f"""<!doctype html>
<html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=1280, height=720"/>
<title>Surprise me</title>
<script src="assets/gsap.min.js"></script>
<style>{css}</style></head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{duration:.3f}" data-width="{WIDTH}" data-height="{HEIGHT}" data-fps="{FPS}">
<div id="film">
  {body}
</div>
</div>
<script>
const tl = gsap.timeline({{ paused: true }});
{chr(10).join(self.js)}
window.__timelines = window.__timelines || {{}};
window.__timelines["main"] = tl;
</script>
</body></html>"""


class Film(_Page):
    """One show's kinetic parts on one page (`layout`): each part's times
    shifted to its place on the page. `timeline`: the saved timeline;
    `shots`: the saved shot list, one per timeline shot (same ids); `pulse`:
    `[{m, chat}]`."""

    def __init__(
        self,
        timeline: Mapping[str, Any],
        shots: Sequence[Mapping[str, Any]],
        *,
        pulse: Sequence[Mapping[str, Any]],
        plan: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.plan = dict(plan or layout(timeline))
        intro_s = float(timeline["intro_s"])
        self.kinetic = [seg for seg in self.plan["segments"] if seg["kind"] == "kinetic"]
        super().__init__(
            offset=intro_s,
            parts=[(intro_s + seg["f0"] / FPS, seg["c0"] / FPS, seg["frames"] / FPS) for seg in self.kinetic]
            or [(intro_s, 0.0, 0.0)],
        )
        self.tl = timeline
        clauses = list(timeline.get("shots") or [])
        if len(clauses) != len(shots) or any(int(c["id"]) != int(s["id"]) for c, s in zip(clauses, shots, strict=True)):
            raise ValueError("The shot list doesn't match the timeline.")
        self.clauses = clauses
        self.shots = [dict(shot) for shot in shots]
        self.pulse = [row for row in pulse if isinstance(row, Mapping)]
        self.html: List[str] = []
        self.sfx: List[Dict[str, Any]] = []
        self.whoosh = 0
        self.pop = 0
        self._last_shake = -9.0
        # The real punchlines are the timeline's windows (`video/punch.py`):
        # a clause the director called a punchline gets a light snap.
        self.light = set()
        for n, shot in enumerate(self.shots):
            if shot.get("emphasis") == "punchline":
                shot["emphasis"] = "build"
                self.light.add(n)
            if shot.get("type") not in TYPES_DRAWN:
                shot["type"] = "kinetic"  # a type this page doesn't draw (an older image shot): the words alone

    # ---- helpers ----
    def ev(self, file: str, t: float, gain: float) -> None:
        self.sfx.append({"file": file, "t": round(max(0.0, t), 3), "gain": gain})

    def sound(self, kind: str, at: float) -> None:
        if kind == "whoosh":
            self.whoosh += 1
            self.ev(f"whoosh{1 + self.whoosh % 2}.flac", at - 0.12, 0.16)
        elif kind == "pop":
            self.pop += 1
            self.ev(f"pop{1 + self.pop % 2}.flac", at + 0.02, 0.35)
        elif kind == "ding":
            self.ev("ding.flac", at + 0.02, 0.14)

    def shake(self, at: float, amp: float = 1.0) -> None:
        if self._last_shake > at - 0.34:
            return
        self._last_shake = at
        prev = (0, 0)
        for n, (x, y) in enumerate(SHAKE):
            self.js.append(
                f'tl.fromTo("#camera",{{x:{prev[0] * amp:.1f},y:{prev[1] * amp:.1f}}},'
                f'{{x:{x * amp:.1f},y:{y * amp:.1f},duration:0.045,ease:"none",immediateRender:false}},{self.at(at + n * 0.045)});'
            )
            prev = (x, y)

    def flash(self, at: float, color: str = "#FFFFFF", peak: float = 0.85) -> None:
        self.set("#flash", {"backgroundColor": color}, at)
        self.js.append(f'tl.fromTo("#flash",{{opacity:{peak}}},{{opacity:0,duration:0.28,ease:"power2.out",immediateRender:false}},{self.at(at)});')

    def words_html(self, pid: str, words: Sequence[str], accent: str, size: int, color: str = INK,
                   accent_color: str = TEAL) -> str:
        spans = []
        for n, word in enumerate(words):
            shade = accent_color if accent and norm(word) == norm(accent) else color
            spans.append(f'<span id="{pid}-w{n}" class="w" style="color:{shade}">{esc(word)}</span>')
        return f'<div class="words" style="font-size:{size}px">' + " ".join(spans) + "</div>"

    # ---- one shot ----
    def shot(self, n: int, cl: Mapping[str, Any], sh: Mapping[str, Any]) -> None:
        start, end = float(cl["start"]), float(cl["end"])
        dur = max(0.05, end - start)
        pid = f"s{n}"
        typ, emph, trans = sh.get("type") or "kinetic", sh.get("emphasis") or "build", sh.get("transition") or "cut"
        words = (sh.get("words") or cl.get("text") or "").split()
        if words:
            words[-1] = words[-1].rstrip(",;:—-") or words[-1]
            if typ == "stamp":
                words[-1] = words[-1].rstrip(".") or words[-1]
        spoken = cl.get("spoken") or []
        times = word_times(words, spoken, start, end)
        chars = len(" ".join(words))
        inner = ""
        if typ == "stamp":
            size = min(fit(chars, big=True), 190)
            inner = (f'<div id="{pid}-stamp" class="stamp" style="font-size:{size}px;color:{TEAL};border-color:{TEAL}">'
                     f'{esc(" ".join(words))}</div>')
        elif typ == "emoji":
            size = min(fit(chars), 130)
            spots = [(-640, -250), (650, -230), (0, 300)]
            emoji = list(sh.get("emoji") or [])[:3] or ["✨"]
            inner = "".join(
                f'<div id="{pid}-e{k}" class="emo" style="left:{960 + spots[k][0] - 110}px;top:{540 + spots[k][1] - 110}px">{esc(e)}</div>'
                for k, e in enumerate(emoji)
            ) + self.words_html(pid, words, sh.get("accent") or "", size)
        elif typ in ("count", "ring") and sh.get("number") and parse_number(str(sh["number"])):
            number = str(sh["number"])
            caption = [w for w in words if number not in w]
            ring = ""
            if typ == "ring":
                ring = ('<svg class="ring" viewBox="0 0 400 400"><circle cx="200" cy="200" r="170" class="ring-bg"/>'
                        f'<circle id="{pid}-arc" cx="200" cy="200" r="170" class="ring-fg" pathLength="1"/></svg>')
            inner = (f'<div class="numwrap">{ring}<div id="{pid}-num" class="num{" ringnum" if ring else ""}">{esc(number)}</div></div>'
                     + (f'<div class="cap">{self.words_html(pid, caption, sh.get("accent") or "", min(fit(len(" ".join(caption))), 96))}</div>'
                        if caption else ""))
            words = caption
            times = word_times(caption, spoken, start, end) if caption else []
        elif typ == "pulse" and self.pulse:
            inner = self.pulse_svg(pid, sh.get("minute")) + (
                f'<div class="cap top">{self.words_html(pid, words, sh.get("accent") or "", min(fit(chars), 96))}</div>')
        elif typ == "chat" and isinstance(sh.get("chat"), Mapping):
            chat = sh["chat"]
            text = str(chat.get("text") or "")
            label = "Asked in the Q&amp;A" if chat.get("kind") == "question" else "In the chat"
            size = 64 if len(text) < 50 else 50 if len(text) < 100 else 42
            inner = (f'<div class="cap top">{self.words_html(pid, words, sh.get("accent") or "", min(fit(chars), 88))}</div>'
                     f'<div id="{pid}-bub" class="bubble"><div class="who"><span class="av">?</span>{label}</div>'
                     f'<div class="msg" style="font-size:{size}px">{esc(text)}</div></div>')
        elif typ == "split" and sh.get("left") and sh.get("right"):
            left, right = str(sh["left"]).split(), str(sh["right"]).split()
            inner = (f'<div class="half l"><div>{self.words_html(pid + "L", left, "", min(fit(len(sh["left"])), 120))}</div></div>'
                     f'<div class="half r"><div>{self.words_html(pid + "R", right, "", min(fit(len(sh["right"])), 120), color=NAVY)}</div></div>'
                     f'<div id="{pid}-vs" class="vs">vs</div>')
        else:
            typ = "kinetic"
            inner = self.words_html(pid, words, sh.get("accent") or "", fit(chars))

        on_page = max(0.05, self.page_time(end) - self.page_time(start))
        self.html.append(f'<div id="{pid}" class="clip shot" data-start="{self.at(start)}" data-duration="{on_page:.3f}" data-track-index="2">'
                         f'<div id="{pid}-stage" class="stage">{inner}</div></div>')
        st = f"#{pid}-stage"
        # Transform and opacity only: a blur is redrawn by the CPU on every frame.
        if trans == "whip":
            self.tw(st, {"x": 420, "skewX": -12, "opacity": 0.35},
                    {"x": 0, "skewX": 0, "opacity": 1, "duration": 0.24, "ease": "expo.out"}, start)
            self.sound("whoosh", start)
        elif trans == "zoom":
            self.tw(st, {"scale": 1.55, "opacity": 0.3}, {"scale": 1, "opacity": 1, "duration": 0.32, "ease": "expo.out"}, start)
            self.sound("whoosh", start)
        elif trans == "drop":
            self.tw(st, {"y": -260}, {"y": 0, "duration": 0.42, "ease": "back.out(1.7)"}, start)
            self.sound("whoosh", start)
        elif trans == "blur":
            self.tw(st, {"opacity": 0, "scale": 1.06}, {"opacity": 1, "scale": 1, "duration": 0.2, "ease": "power3.out"}, start)
        elif trans == "flash":
            self.flash(start, TEAL, 0.7)
        elif trans == "shake":
            self.shake(start + 0.05, 0.8)
        if n in self.light:
            self.js.append(f'tl.fromTo("{st}",{{scale:1.16}},{{scale:1,duration:0.3,ease:"back.out(2)",immediateRender:false}},{self.at(start)});')
            if trans != "flash":
                self.flash(start, "#FFFFFF", 0.3)
            self.sound("pop", start)
        self.after_arrival(pid, st, start, end, typ, emph, times, cl, sh, dur)

    def after_arrival(self, pid: str, st: str, start: float, end: float, typ: str, emph: str, times: List[float],
                      cl: Mapping[str, Any], sh: Mapping[str, Any], dur: float) -> None:
        if typ == "kinetic":
            self.reveal_words(pid, times, "rise" if emph == "build" else "pop")
        elif typ == "stamp":
            t0 = times[0] if times else start
            self.tw(f"#{pid}-stamp", {"opacity": 0, "scale": 2.6, "rotation": -2},
                    {"opacity": 1, "scale": 1, "rotation": -7, "duration": 0.18, "ease": "power4.in"}, t0)
            self.shake(t0 + 0.18, 0.7)
            self.sound("pop", t0 + 0.18)
        elif typ == "emoji":
            self.reveal_words(pid, times, "pop")
            for k in range(len(list(sh.get("emoji") or [])[:3] or ["x"])):
                t = start + 0.08 + k * 0.12
                self.tw(f"#{pid}-e{k}", {"scale": 0, "rotation": -40 + 30 * k, "opacity": 0},
                        {"scale": 1, "rotation": [-14, 12, -6][k], "opacity": 1, "duration": 0.5, "ease": "elastic.out(1,0.5)"}, t)
                if k == 0:
                    self.sound("pop", t)
        elif typ in ("count", "ring"):
            number = str(sh["number"])
            parsed = parse_number(number) or {"kind": "num", "pre": "", "to": 0, "dec": 0, "post": ""}
            heard = None
            for word in cl.get("spoken") or []:
                if norm(number)[:2] and norm(word.get("w")).startswith(norm(number)[:2]):
                    heard = float(word["t"])
                    break
            land = min(end - 0.15, (heard + 0.25) if heard is not None else start + 1.1)
            land = max(land, start + 0.2)
            begin = max(start + 0.05, land - 1.3)
            self.tw(f"#{pid}-num", {"scale": 0.6, "opacity": 0}, {"scale": 1, "opacity": 1, "duration": 0.25, "ease": "expo.out"}, start)
            self.js.append(self.count_js(pid, parsed, begin, max(0.1, land - begin)))
            self.tw(f"#{pid}-num", {"scale": 1.18}, {"scale": 1, "duration": 0.3, "ease": "back.out(3)", "immediateRender": False}, land)
            self.sound("ding", land)
            if typ == "ring":
                share = min(1.0, float(parsed["to"]) / 100)
                self.tw(f"#{pid}-arc", {"strokeDashoffset": 1},
                        {"strokeDashoffset": 1 - share, "duration": max(0.1, land - begin), "ease": "power2.out"}, begin)
            if times:
                self.reveal_words(pid, times, "pop")
        elif typ == "pulse":
            draw = min(1.2, dur * 0.6)
            self.tw(f"#{pid}-line", {"strokeDashoffset": 1}, {"strokeDashoffset": 0, "duration": draw, "ease": "power2.inOut"}, start)
            self.tw(f"#{pid}-area", {"opacity": 0}, {"opacity": 1, "duration": 0.5}, start + draw * 0.6)
            self.reveal_words(pid, times, "pop")
            if sh.get("minute") is not None and dur > 1.0 and f'id="{pid}-mk"' in self.html[-1]:
                tz = start + draw
                self.tw(f"#{pid}-mk", {"scale": 0, "opacity": 0}, {"scale": 1, "opacity": 1, "duration": 0.35, "ease": "back.out(3)"}, tz)
                self.tw(f"#{pid}-zoom", {"scale": 1}, {"scale": 1.9, "duration": max(0.4, min(0.9, end - tz - 0.1)), "ease": "power2.inOut"}, tz)
                self.sound("pop", tz)
        elif typ == "chat":
            self.reveal_words(pid, times, "pop")
            self.tw(f"#{pid}-bub", {"scale": 0.5, "opacity": 0, "y": 80},
                    {"scale": 1, "opacity": 1, "y": 0, "duration": 0.45, "ease": "back.out(1.8)"}, start + 0.1)
            self.sound("pop", start + 0.1)
        elif typ == "split":
            spoken = cl.get("spoken") or []
            lt = word_times(str(sh["left"]).split(), spoken, start, end)
            rt = word_times(str(sh["right"]).split(), spoken, start, end)
            self.tw(f"#{pid} .half.l", {"xPercent": -100}, {"xPercent": 0, "duration": 0.3, "ease": "expo.out"}, start)
            self.tw(f"#{pid} .half.r", {"xPercent": 100}, {"xPercent": 0, "duration": 0.3, "ease": "expo.out"}, max(start + 0.15, rt[0] - 0.2))
            self.reveal_words(pid + "L", lt, "slam")
            self.reveal_words(pid + "R", rt, "slam")
            self.tw(f"#{pid}-vs", {"scale": 0}, {"scale": 1, "duration": 0.3, "ease": "back.out(3)"}, max(start + 0.2, rt[0] - 0.1))
            self.sound("whoosh", start)
        if cl.get("hold_after"):
            self.hold(st, cl, end)

    def hold(self, st: str, cl: Mapping[str, Any], end: float) -> None:
        """The held beat before a punchline: the picture freezes grey and
        pushes in slowly, the edges darken. At `end` the punchline's window
        takes the screen: everything is set back at once, in the still
        frames the renderer throws away, before the next part."""
        h0 = end - float(cl["hold_after"])
        self.tw("#camera", {"filter": "grayscale(0) brightness(1)"},
                {"filter": "grayscale(1) brightness(0.8)", "duration": 0.3, "ease": "power2.out", "immediateRender": False}, h0)
        self.tw(st, {"scale": 1}, {"scale": 1.07, "duration": float(cl["hold_after"]), "ease": "power1.in", "immediateRender": False}, h0)
        self.tw("#hold", {"opacity": 0}, {"opacity": 0.45, "duration": 0.3, "immediateRender": False}, h0)
        self.set("#hold", {"opacity": 0}, end)
        self.set("#camera", {"filter": "grayscale(0) brightness(1)"}, end)

    def count_js(self, pid: str, number: Mapping[str, Any], at: float, duration: float) -> str:
        if number["kind"] == "clock":
            shown = 'Math.floor(v/60)+":"+String(Math.floor(v%60)).padStart(2,"0")'
        else:
            shown = f'(v).toFixed({int(number["dec"])})' + ('.replace(/\\B(?=(\\d{3})+(?!\\d))/g, ",")' if number.get("comma") else "")
        return (f'(function(){{const o={{v:0}};const el=document.getElementById({_js(pid + "-num")});'
                f'tl.fromTo(o,{{v:0}},{{v:{float(number["to"])},duration:{duration:.3f},ease:"power3.out",onUpdate:function(){{const v=o.v;'
                f'el.textContent={_js(number["pre"])}+{shown}+{_js(number["post"])};}}}},{self.at(at)});}})();')

    def pulse_svg(self, pid: str, minute: Any) -> str:
        points = self.pulse
        top = max(1, max(int(p.get("chat") or 0) for p in points))
        x0, x1, y0, y1 = 120, 1800, 900, 430
        span = max(1, len(points) - 1)
        xy = [(x0 + (x1 - x0) * i / span, y0 - (y0 - y1) * int(p.get("chat") or 0) / top) for i, p in enumerate(points)]
        path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in xy)
        area = path + f" L{x1},{y0} L{x0},{y0} Z"
        marker, origin = "", "50% 60%"
        if minute is not None:
            i = next((k for k, p in enumerate(points) if p.get("m") == minute), None)
            if i is not None:
                x, y = xy[i]
                marker = (f'<g id="{pid}-mk" style="transform-origin:{x:.0f}px {y:.0f}px"><line x1="{x:.1f}" y1="{y0}" x2="{x:.1f}" '
                          f'y2="{y:.1f}" class="mkline"/><circle cx="{x:.1f}" cy="{y:.1f}" r="16" class="mkdot"/></g>')
                origin = f"{x / W * 100:.1f}% {y / H * 100:.1f}%"
        return (f'<div id="{pid}-zoom" class="pulsewrap" style="transform-origin:{origin}"><svg viewBox="0 0 {W} {H}" class="pulse">'
                f'<defs><linearGradient id="{pid}-g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{TEAL}" stop-opacity="0.45"/>'
                f'<stop offset="1" stop-color="{TEAL}" stop-opacity="0"/></linearGradient></defs>'
                f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y0}" class="axis"/>'
                f'<path id="{pid}-area" d="{area}" fill="url(#{pid}-g)"/>'
                f'<path id="{pid}-line" d="{path}" class="pline" pathLength="1"/>{marker}'
                f'<text x="{x0}" y="{y0 + 60}" class="src">Chat messages per minute · start → end</text></svg></div>')

    # ---- the page ----
    def bumper_sounds(self, lead: float, join: float) -> None:
        """The intro's and the outro's sounds, on the whole video's clock:
        the intro starts at 0, the outro at `join`."""
        jingle_in, jingle_out = jingle_files()
        self.ev(jingle_in, 0.15, JINGLE_GAIN)
        self.sound("whoosh", lead - 0.2)
        self.ev(jingle_out, join + SIGNOFF_S - 0.35, JINGLE_GAIN)

    def build(self, css: str) -> Optional[str]:
        """The kinetic parts' page (None when the middle has none), its
        sound effects in `sfx`, every punchline's "dun" included."""
        for n, (cl, sh) in enumerate(zip(self.clauses, self.shots, strict=True)):
            self.shot(n, cl, sh)
        parts = spans(self.tl)
        self.bumper_sounds(parts["intro"], parts["join"])
        middle = max(1, self.plan["middle_frames"])
        for seg in self.plan["segments"]:
            if seg["kind"] == "punch":
                self.ev("dun.flac", parts["intro"] + seg["f0"] / FPS + 0.09, DUN_GAIN)
        # The bar runs over the middle: here, in each part, from its share to the next.
        for k, seg in enumerate(self.kinetic):
            frm, to = seg["f0"] / middle, seg["f1"] / middle
            self.js.append(
                f'tl.fromTo("#progress",{{scaleX:{frm:.5f}}},{{scaleX:{to:.5f},duration:{seg["frames"] / FPS:.3f},'
                f'ease:"none",immediateRender:{"true" if k == 0 else "false"}}},{seg["c0"] / FPS:.3f});'
            )
        self.sfx.sort(key=lambda event: event["t"])
        if not self.kinetic:
            return None
        body = (f'<div id="camera">{"".join(self.html)}</div>'
                '<div id="hold"></div><div id="flash"></div><div id="progress"></div>')
        return self.page(css, self.plan["page_frames"] / FPS, body)


class Bumper(_Page):
    """The intro or the outro: nothing from a show, the same on every video."""

    def __init__(self, mark_svg: str) -> None:
        super().__init__()
        self.mark = _mark(mark_svg)

    def intro(self, css: str, lead: float) -> str:
        letters = "".join(f'<span id="it-{k}">{esc(c) if c != " " else "&nbsp;"}</span>' for k, c in enumerate("Surprise me"))
        body = (f'<div id="camera"><div id="intro" class="clip" data-start="0" data-duration="{lead:.3f}" data-track-index="1">'
                f'<div id="intro-in" class="center"><div id="intro-mark" class="mark big">{self.mark}</div>'
                f'<div class="title">{letters}<span id="it-bang" class="bang">!</span></div>'
                f'<div id="intro-sweep" class="sweep"></div><div id="intro-tag" class="tag">{esc(TAGLINE)}</div></div></div></div>')
        self.tw("#intro-mark", {"scale": 0, "rotation": -120}, {"scale": 1, "rotation": 0, "duration": 0.55, "ease": "back.out(2)"}, 0.15)
        for k in range(len("Surprise me")):
            self.tw(f"#it-{k}", {"opacity": 0, "y": 60, "filter": "blur(6px)"},
                    {"opacity": 1, "y": 0, "filter": "blur(0px)", "duration": 0.22, "ease": "expo.out"}, 0.5 + k * 0.045)
        self.tw("#it-bang", {"scale": 0, "rotation": -40}, {"scale": 1, "rotation": 0, "duration": 0.4, "ease": "back.out(3)"}, 1.15)
        self.tw("#intro-sweep", {"scaleX": 0}, {"scaleX": 1, "duration": 0.45, "ease": "power3.inOut"}, 1.35)
        self.tw("#intro-tag", {"opacity": 0, "filter": "blur(8px)"}, {"opacity": 1, "filter": "blur(0px)", "duration": 0.3}, 1.75)
        self.tw("#intro-in", {"x": 0, "filter": "blur(0px)", "opacity": 1},
                {"x": -420, "filter": "blur(16px)", "opacity": 0, "duration": 0.3, "ease": "power3.in"}, lead - 0.32)
        return self.page(css, lead, body)

    def outro(self, css: str, length: float) -> str:
        words = SIGNOFF.split()
        spans_html = " ".join(f'<span id="so-w{k}" class="w" style="color:{TEAL if k >= 2 else INK}">{esc(w)}</span>'
                              for k, w in enumerate(words))
        body = (f'<div id="camera"><div id="outro" class="clip" data-start="0" data-duration="{SIGNOFF_S}" data-track-index="1">'
                f'<div id="outro-in" class="center"><div class="signoff">{spans_html}</div></div></div>'
                f'<div id="endcard" class="clip" data-start="{SIGNOFF_S}" data-duration="{length - SIGNOFF_S:.3f}" '
                f'data-track-index="1"><div class="center"><div id="end-mark" class="mark big">{self.mark}</div>'
                f'<div id="end-name" class="endname">StormIQ</div><div id="end-sub" class="endsub">Surprise me · {esc(TAGLINE)}</div>'
                '</div></div></div>')
        self.reveal_words("so", [0.15 + k * 0.16 for k in range(len(words))], "rise")
        self.tw("#outro-in", {"opacity": 1, "filter": "blur(0px)"},
                {"opacity": 0, "filter": "blur(10px)", "duration": 0.2, "immediateRender": False}, SIGNOFF_S - 0.22)
        self.tw("#end-mark", {"scale": 0, "rotation": -120}, {"scale": 1, "rotation": 0, "duration": 0.55, "ease": "back.out(2)"}, SIGNOFF_S + 0.05)
        self.tw("#end-name", {"opacity": 0, "filter": "blur(8px)"}, {"opacity": 1, "filter": "blur(0px)", "duration": 0.3}, SIGNOFF_S + 0.35)
        self.tw("#end-sub", {"opacity": 0}, {"opacity": 1, "duration": 0.4}, SIGNOFF_S + 0.7)
        return self.page(css, length, body)


def _mark(svg: str) -> str:
    return re.sub(r'width="\d+" height="\d+"', 'width="100%" height="100%"', svg, count=1)


BUMPER_KINDS = ("intro", "outro")


def bumper_spans(timeline: Mapping[str, Any]) -> List[Tuple[str, float]]:
    """The bumpers a show needs: `[(kind, seconds)]`, on whole frames."""
    parts = spans(timeline)
    return [("intro", parts["intro"]), ("outro", parts["outro"])]


def build_bumper(kind: str, seconds: float, *, mark_svg: str, css: str) -> str:
    """The intro's or the outro's page, `seconds` long. Nothing from a show:
    the same bytes for the same kind, length and design."""
    if kind == "intro":
        return Bumper(mark_svg).intro(css, float(seconds))
    if kind == "outro":
        if float(seconds) <= SIGNOFF_S:
            raise ValueError("The outro is shorter than its sign-off.")
        return Bumper(mark_svg).outro(css, float(seconds))
    raise ValueError(f"Unknown bumper: {kind}")


def build_film(
    timeline: Mapping[str, Any],
    shots: Sequence[Mapping[str, Any]],
    *,
    pulse: Sequence[Mapping[str, Any]],
    css: str,
    plan: Optional[Mapping[str, Any]] = None,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """The kinetic parts' page (`layout(timeline)["page_frames"]` long; None
    when the middle is only punchlines) and every sound effect of the whole
    video to mix (`[{file, t, gain}]`, the intro's, the outro's and the
    punchlines' included)."""
    film = Film(timeline, shots, pulse=pulse, plan=plan)
    page = film.build(css)
    return page, film.sfx
