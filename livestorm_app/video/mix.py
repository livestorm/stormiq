"""Surprise me's soundtrack and the final file: one ffmpeg call that lays
each voice clip at its place on the timeline, brings the voice to -16 LUFS,
adds the sound effects and the jingle at their gains (no music bed), keeps
the peaks under the limiter, and muxes it with HyperFrames' silent picture
(H.264 copied, AAC, `+faststart` so a browser plays it before it has it all).

`mix_command` is pure: it returns the argument list.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Mapping, Sequence, Tuple

VOICE_LUFS = -16
TRUE_PEAK = -1.5
LIMIT = 0.89
AUDIO_RATE = 48_000
AUDIO_BITRATE = "96k"


def mix_command(
    ffmpeg: str,
    picture: Path,
    voice: Sequence[Tuple[Path, float]],
    effects: Sequence[Mapping[str, object]],
    effects_dir: Path,
    total: float,
    out: Path,
) -> List[str]:
    """`voice`: each clip's file and start (s); `effects`: `{file, t, gain}`
    with `file` under `effects_dir`; `total`: the video's length (s)."""
    if not voice:
        raise ValueError("The video needs its voice.")
    args: List[str] = [ffmpeg, "-v", "error", "-y", "-i", str(picture)]
    graph: List[str] = []
    voice_labels: List[str] = []
    index = 1
    for path, start in voice:
        args += ["-i", str(path)]
        ms = max(0, int(round(float(start) * 1000)))
        graph.append(f"[{index}:a]aformat=sample_rates={AUDIO_RATE}:channel_layouts=stereo,adelay={ms}|{ms}[v{index}]")
        voice_labels.append(f"[v{index}]")
        index += 1
    graph.append(
        f"{''.join(voice_labels)}amix=inputs={len(voice_labels)}:normalize=0,"
        f"loudnorm=I={VOICE_LUFS}:TP={TRUE_PEAK}:LRA=11,aresample={AUDIO_RATE}[voice]"
    )
    labels = ["[voice]"]
    for event in effects:
        args += ["-i", str(effects_dir / str(event["file"]))]
        ms = max(0, int(round(float(event["t"]) * 1000)))  # type: ignore[arg-type]
        gain = float(event["gain"])  # type: ignore[arg-type]
        graph.append(f"[{index}:a]aformat=sample_rates={AUDIO_RATE}:channel_layouts=stereo,volume={gain:.3f},adelay={ms}|{ms}[e{index}]")
        labels.append(f"[e{index}]")
        index += 1
    graph.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,alimiter=limit={LIMIT}:level=false,apad,atrim=0:{float(total):.3f}[a]")
    args += [
        "-filter_complex", ";".join(graph),
        "-map", "0:v:0", "-map", "[a]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(AUDIO_RATE),
        "-t", f"{float(total):.3f}",
        "-movflags", "+faststart",
        str(out),
    ]
    return args
