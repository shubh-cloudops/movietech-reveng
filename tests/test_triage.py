"""Watcher Pass A tests with a fake Gemini client: no API key, no cost."""

from types import SimpleNamespace

import pytest

from movietech.schemas import Manifest, Segment, TriageVerdict
from movietech.watcher_triage import BudgetExceeded, triage


class FakeClient:
    """Mimics client.models.generate_content and records every call."""

    def __init__(self, prompt_tokens=1000, output_tokens=20):
        self.calls = []
        self.models = self
        self.usage = SimpleNamespace(prompt_token_count=prompt_tokens,
                                     candidates_token_count=output_tokens, thoughts_token_count=0)

    def generate_content(self, model, contents, config):
        self.calls.append(contents)
        verdict = "yes" if "Activate" in contents[-1] else "no"
        return SimpleNamespace(parsed=TriageVerdict(verdict=verdict, reason="test"),
                               usage_metadata=self.usage)


@pytest.fixture
def run_dir(tmp_path):
    (tmp_path / "keyframes").mkdir()
    segments = []
    for i, lines in enumerate([["Activate the HUD."], [], ["Nice weather."]], start=1):
        frame = f"keyframes/S00{i}_1.jpg"
        (tmp_path / frame).write_bytes(b"\xff\xd8fakejpeg")
        segments.append(Segment(segment_id=f"S00{i}", start=i * 30, end=i * 30 + 30, shot_count=1,
                                keyframes=[frame], transcript=lines))
    manifest = Manifest(source="clip.mp4", duration=120, segments=segments)
    (tmp_path / "manifest.json").write_text(manifest.model_dump_json())
    return tmp_path


def test_triage_writes_one_verdict_per_segment(run_dir):
    results = triage(run_dir, client=FakeClient(), model="gemini-3.8-flash", max_usd=1.0)
    assert [r.verdict for r in results] == ["yes", "no", "no"]
    assert len((run_dir / "triage.jsonl").read_text().splitlines()) == 3


def test_rerun_skips_finished_segments(run_dir):
    triage(run_dir, client=FakeClient(), model="gemini-3.8-flash", max_usd=1.0)
    second = FakeClient()
    results = triage(run_dir, client=second, model="gemini-3.8-flash", max_usd=1.0)
    assert second.calls == []
    assert len(results) == 3


def test_budget_cap_stops_run_and_keeps_progress(run_dir):
    # 1M prompt tokens per call at $0.75/M: the second call passes a $1.00 cap.
    client = FakeClient(prompt_tokens=1_000_000)
    with pytest.raises(BudgetExceeded):
        triage(run_dir, client=client, model="gemini-3.8-flash", max_usd=1.0)
    assert len(client.calls) == 2
    assert len((run_dir / "triage.jsonl").read_text().splitlines()) == 2
