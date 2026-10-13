"""Watcher Pass B and cost ledger tests with a fake Gemini client: no API key, no cost."""

import shutil
import subprocess
from types import SimpleNamespace

import pytest

from movietech.costs import BudgetExceeded, summarise
from movietech.schemas import DeepLook, Manifest, Observation, Segment, TriageResult
from movietech.watcher_deep import deep_look

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


class FakeClient:
    """Mimics client.models.generate_content and records every call."""

    def __init__(self, prompt_tokens=5000, output_tokens=300, thinking_tokens=700):
        self.calls = []
        self.models = self
        self.usage = SimpleNamespace(prompt_token_count=prompt_tokens,
                                     candidates_token_count=output_tokens,
                                     thoughts_token_count=thinking_tokens)

    def generate_content(self, model, contents, config):
        self.calls.append(contents)
        look = DeepLook(
            shows_software=True, summary="A HUD tracks a target.",
            observations=[Observation(at=4.0, what_happens="HUD draws a box round a face",
                                      interface="HUD", user_input="automatic",
                                      evidence="observed")],
            capabilities=["face tracking overlay"],
        )
        return SimpleNamespace(parsed=look, usage_metadata=self.usage)


@pytest.fixture
def run(tmp_path):
    """A 30s video, three 10s segments, and a triage file flagging S001 and S003."""
    video = tmp_path / "film.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=s=320x240:d=30:r=10", str(video)], check=True)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    segments = [Segment(segment_id=f"S00{i}", start=(i - 1) * 10, end=i * 10, shot_count=1,
                        keyframes=[], transcript=[]) for i in (1, 2, 3)]
    (run_dir / "manifest.json").write_text(
        Manifest(source="film.mp4", duration=30, segments=segments).model_dump_json())
    verdicts = {"S001": "yes", "S002": "no", "S003": "unsure"}
    (run_dir / "triage.jsonl").write_text("".join(
        TriageResult(segment_id=s.segment_id, start=s.start, end=s.end,
                     verdict=verdicts[s.segment_id], reason="test").model_dump_json() + "\n"
        for s in segments))
    return run_dir, video


def test_only_flagged_segments_are_sent(run):
    run_dir, video = run
    client = FakeClient()
    results = deep_look(run_dir, video, client=client, model="gemini-3.1-pro-preview")
    assert [r.segment_id for r in results] == ["S001", "S003"]
    assert len(client.calls) == 2
    assert (run_dir / "clips" / "S001.mp4").stat().st_size > 0
    assert not (run_dir / "clips" / "S002.mp4").exists()


def test_observation_time_is_converted_to_film_time(run):
    run_dir, video = run
    results = deep_look(run_dir, video, client=FakeClient(), model="gemini-3.1-pro-preview")
    assert results[1].observations[0].at == 24.0  # S003 starts at 20s, obs at 4s into clip


def test_rerun_skips_finished_segments(run):
    run_dir, video = run
    deep_look(run_dir, video, client=FakeClient(), model="gemini-3.1-pro-preview")
    second = FakeClient()
    results = deep_look(run_dir, video, client=second, model="gemini-3.1-pro-preview")
    assert second.calls == []
    assert len(results) == 2


def test_budget_cap_stops_run_and_keeps_progress(run):
    run_dir, video = run
    # 1M input tokens at $2/M = $2 per call: the first call passes a $1.00 cap.
    client = FakeClient(prompt_tokens=1_000_000)
    with pytest.raises(BudgetExceeded):
        deep_look(run_dir, video, client=client, model="gemini-3.1-pro-preview", max_usd=1.0)
    assert len(client.calls) == 1
    assert len((run_dir / "deep.jsonl").read_text().splitlines()) == 1


def test_ledger_logs_every_call(run):
    run_dir, video = run
    deep_look(run_dir, video, client=FakeClient(), model="gemini-3.1-pro-preview")
    totals = summarise(run_dir)
    assert totals["deep"]["calls"] == 2
    # per call: 5000 * $2/M + (300 + 700) * $12/M = $0.022
    assert totals["deep"]["usd"] == pytest.approx(0.044)
