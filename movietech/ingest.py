"""Stage 0, the Ingestor: plain code, no LLM.

Splits a local video into timestamped segments, saves a few keyframes per segment,
and attaches the subtitle lines spoken in each one. Output: <out_dir>/manifest.json.
"""

import json
import subprocess
from pathlib import Path

import srt
from scenedetect import ContentDetector, detect

from movietech import config
from movietech.schemas import Manifest, Segment


def probe_duration(video: Path) -> float:
    """Film length in seconds, read by ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
        check=True, capture_output=True, text=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def detect_shots(video: Path, duration: float) -> list[tuple[float, float]]:
    """Camera cuts found by PySceneDetect. A video with no cuts is one shot."""
    scenes = detect(str(video), ContentDetector())
    shots = [(s.seconds, e.seconds) for s, e in scenes]
    return shots or [(0.0, duration)]


def group_shots(
    shots: list[tuple[float, float]], min_s: float, max_s: float
) -> list[tuple[float, float, int]]:
    """Merge consecutive shots into segments of roughly min_s..max_s seconds.

    Returns (start, end, shot_count). A single shot longer than max_s is cut into pieces.
    """
    pieces: list[tuple[float, float]] = []
    for start, end in shots:
        while end - start > max_s:
            pieces.append((start, start + max_s))
            start += max_s
        pieces.append((start, end))

    segments: list[tuple[float, float, int]] = []
    cur_start, cur_end, count = None, None, 0
    for start, end in pieces:
        if cur_start is not None and end - cur_start > max_s:
            segments.append((cur_start, cur_end, count))
            cur_start, count = None, 0
        if cur_start is None:
            cur_start = start
        cur_end, count = end, count + 1
        if cur_end - cur_start >= min_s:
            segments.append((cur_start, cur_end, count))
            cur_start, count = None, 0
    if cur_start is not None:
        segments.append((cur_start, cur_end, count))
    return segments


def extract_keyframes(video: Path, start: float, end: float, n: int, out_dir: Path, stem: str) -> list[Path]:
    """Save n evenly spaced JPEG frames from inside [start, end]."""
    paths = []
    for i in range(n):
        t = start + (end - start) * (i + 1) / (n + 1)
        path = out_dir / f"{stem}_{i + 1}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale={config.KEYFRAME_WIDTH}:-2", "-q:v", "4", str(path)],
            check=True,
        )
        paths.append(path)
    return paths


def load_subtitles(video: Path, srt_path: Path | None) -> list[srt.Subtitle]:
    """Subtitles from a sidecar .srt if given, else the video's first embedded subtitle track."""
    if srt_path:
        text = srt_path.read_text(encoding="utf-8-sig", errors="replace")
    else:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(video), "-map", "0:s:0", "-f", "srt", "-"],
            check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if out.returncode != 0:
            return []  # no embedded subtitles; Whisper transcription is a later step
        text = out.stdout
    return list(srt.parse(text))


def lines_between(subs: list[srt.Subtitle], start: float, end: float) -> list[str]:
    """Subtitle lines that overlap [start, end], flattened to one line each."""
    return [
        " ".join(s.content.split())
        for s in subs
        if s.start.total_seconds() < end and s.end.total_seconds() > start
    ]


def ingest(video: Path, out_dir: Path, srt_path: Path | None = None) -> Manifest:
    """Run the whole Ingestor and write manifest.json."""
    frames_dir = out_dir / "keyframes"
    frames_dir.mkdir(parents=True, exist_ok=True)

    duration = probe_duration(video)
    shots = detect_shots(video, duration)
    subs = load_subtitles(video, srt_path)

    segments = []
    for i, (start, end, count) in enumerate(
        group_shots(shots, config.SEGMENT_MIN_S, config.SEGMENT_MAX_S), start=1
    ):
        seg_id = f"S{i:03d}"
        frames = extract_keyframes(video, start, end, config.KEYFRAMES_PER_SEGMENT, frames_dir, seg_id)
        segments.append(Segment(
            segment_id=seg_id,
            start=round(start, 2),
            end=round(end, 2),
            shot_count=count,
            keyframes=[p.relative_to(out_dir).as_posix() for p in frames],
            transcript=lines_between(subs, start, end),
        ))

    manifest = Manifest(source=video.name, duration=round(duration, 2), segments=segments)
    (out_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest
