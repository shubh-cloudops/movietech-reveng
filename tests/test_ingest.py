"""End-to-end Ingestor test on a generated 60s video: no download, no API key."""

import shutil
import subprocess

import pytest

from movietech.ingest import ingest
from movietech.schemas import Manifest

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

SRT = """1
00:00:02,000 --> 00:00:04,000
System online.

2
00:00:40,000 --> 00:00:42,000
Run the scan.
"""


@pytest.fixture
def video(tmp_path):
    """Three solid-colour shots (25s red, 15s blue, 20s green) so there are two hard cuts."""
    path = tmp_path / "clip.mp4"
    inputs = []
    for colour, secs in [("red", 25), ("blue", 15), ("green", 20)]:
        inputs += ["-f", "lavfi", "-i", f"color=c={colour}:s=320x240:d={secs}:r=10"]
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *inputs,
         "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1[v]", "-map", "[v]", str(path)],
        check=True,
    )
    return path


def test_ingest_builds_manifest(video, tmp_path):
    srt_path = tmp_path / "clip.srt"
    srt_path.write_text(SRT, encoding="utf-8")
    out = tmp_path / "run"

    manifest = ingest(video, out, srt_path)

    assert abs(manifest.duration - 60) < 1
    # 25s red shot is one segment; blue (15s) + green (20s) merge into the second.
    assert [(round(s.start), round(s.end)) for s in manifest.segments] == [(0, 25), (25, 60)]
    assert manifest.segments[0].transcript == ["System online."]
    assert manifest.segments[1].transcript == ["Run the scan."]
    for seg in manifest.segments:
        assert len(seg.keyframes) == 3
        assert all((out / k).stat().st_size > 0 for k in seg.keyframes)
    # manifest.json on disk round-trips through the schema
    assert Manifest.model_validate_json((out / "manifest.json").read_text()) == manifest


def test_ingest_without_subtitles(video, tmp_path):
    manifest = ingest(video, tmp_path / "run")
    assert all(seg.transcript == [] for seg in manifest.segments)
