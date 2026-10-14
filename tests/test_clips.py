"""Clips-and-notes input tests: fake Gemini, temp files. No key, no cost, no download."""

from types import SimpleNamespace

import pytest
from google.genai import errors

from movietech.costs import summarise
from movietech.schemas import DeepLook, DeepLookResult, Observation
from movietech.watcher_clips import read_clips, read_notes, watch_clips

NOTES = """# Iron Man (2008): scene notes template

## Scene N
- **Time:** 00:00:00 - 00:00:00
- **System:** (your name for it)
- **What happens:** (1 to 3 sentences)

## Scene 1
- **Time:** 00:42:10 - 00:42:55
- **System:** JARVIS
- **What happens:** He asks JARVIS to run a flight test and the HUD shows thrust levels.
- **Input the system got:** voice
- **Output the system gave:** speech and display
- **On-screen text:** "THRUST 30%"
- **Dialogue with the system:** "Run a flight test."
- **Anything notable:** none

## Example (made up, to show the level of detail)
- **Time:** 01:02:10 - 01:02:45
- **System:** Workshop display
- **What happens:** He spreads his hands.
"""

CLIPS = """# official clips
https://www.youtube.com/watch?v=aaa | Suit test | 00:42:00
https://youtu.be/bbb
"""


class FakeGemini:
    """Answers each clip; a URL containing 'private' fails like a video Gemini can't open."""

    def __init__(self):
        self.models, self.calls = self, []

    def generate_content(self, model, contents, config):
        uri = contents[0].file_data.file_uri
        self.calls.append((uri, contents[1]))
        if "private" in uri:
            raise errors.ClientError(403, {"error": {"message": "video is private"}})
        look = DeepLook(shows_software=True, summary="HUD shows thrust.",
                        observations=[Observation(at=5.0, what_happens="thrust bar fills",
                                                  interface="HUD", user_input="voice",
                                                  evidence="observed")],
                        capabilities=["voice-driven test console"])
        usage = SimpleNamespace(prompt_token_count=50_000, candidates_token_count=1_000,
                                thoughts_token_count=2_000)
        return SimpleNamespace(parsed=look, usage_metadata=usage)


@pytest.fixture
def inputs(tmp_path):
    (tmp_path / "notes.md").write_text(NOTES, encoding="utf-8")
    (tmp_path / "clips.txt").write_text(CLIPS, encoding="utf-8")
    return tmp_path


def test_notes_skip_placeholder_and_example(inputs):
    [n] = read_notes(inputs / "notes.md")
    assert (n.segment_id, n.start, n.end, n.model) == ("N01", 2530.0, 2575.0, "your notes")
    obs = n.observations[0]
    assert (obs.user_input, obs.evidence, obs.at) == ("voice", "observed", 2530.0)
    assert '"THRUST 30%"' in obs.what_happens and "Run a flight test" in obs.what_happens
    assert "Notable" not in obs.what_happens  # "none" is left out


def test_clip_list(inputs):
    assert read_clips(inputs / "clips.txt") == [
        ("https://www.youtube.com/watch?v=aaa", "Suit test", 2520.0),
        ("https://youtu.be/bbb", "clip 2", 0.0)]


def test_clip_list_rejects_other_links(tmp_path):
    (tmp_path / "c.txt").write_text("https://example.com/film.mp4 | pirated\n")
    with pytest.raises(ValueError, match="not a YouTube link"):
        read_clips(tmp_path / "c.txt")


def test_writes_deep_jsonl_for_cards(inputs):
    run = inputs / "run"
    gem = FakeGemini()
    results, warnings = watch_clips(run, inputs / "clips.txt", inputs / "notes.md",
                                    client=gem, model="gemini-3.1-pro-preview")
    assert warnings == []
    assert [r.segment_id for r in results] == ["N01", "Y01", "Y02"]
    y1 = results[1]
    assert y1.observations[0].at == 2525.0  # 00:42:00 + 5s into the clip
    assert y1.summary.startswith("[Suit test]") and 'titled "Suit test"' in gem.calls[0][1]
    assert "Ignore hardware" in gem.calls[0][1]  # software, UI and design only
    lines = (run / "deep.jsonl").read_text().splitlines()
    assert [DeepLookResult.model_validate_json(x).segment_id for x in lines] == ["N01", "Y01",
                                                                                 "Y02"]
    # Gemini Pro: 50,000 * $2/M + 3,000 * $12/M = $0.136 per clip
    assert summarise(run)["clips"]["usd"] == pytest.approx(0.272)


def test_rerun_skips_finished_and_retries_failed(inputs):
    run = inputs / "run"
    (inputs / "clips.txt").write_text("https://youtu.be/private1\nhttps://youtu.be/ok\n")
    _, warnings = watch_clips(run, inputs / "clips.txt", client=FakeGemini(),
                              model="gemini-3.1-pro-preview")
    assert len(warnings) == 1 and "Y01 skipped" in warnings[0] and "private" in warnings[0]
    again = FakeGemini()
    (inputs / "clips.txt").write_text("https://youtu.be/fixed\nhttps://youtu.be/ok\n")
    results, _ = watch_clips(run, inputs / "clips.txt", client=again,
                             model="gemini-3.1-pro-preview")
    assert [c[0] for c in again.calls] == ["https://youtu.be/fixed"]  # Y02 was already done
    assert sorted(r.segment_id for r in results) == ["Y01", "Y02"]


def test_needs_an_input(tmp_path):
    with pytest.raises(ValueError, match="--clips, --notes"):
        watch_clips(tmp_path / "run")
