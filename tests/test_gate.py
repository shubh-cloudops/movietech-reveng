"""Human gate tests: Vision Brief parsing and card selection. No API calls."""

from pathlib import Path

import pytest

from movietech.gate import BriefError, parse_brief, run_gate
from movietech.schemas import CapabilityCard, CapabilityCards, EvidenceRef, GateResult

TEMPLATE = Path(__file__).parent.parent / "templates" / "vision-brief-template.md"

GOOD = """# Vision Brief: Live captions

## Who uses it
<!-- hint the template leaves behind -->
Me, a cloud technician.

## Use case
Show live captions of a meeting.

## Where it runs
Web

## Must-haves
- Captions within 2 seconds
- Works in Chrome

## Out of scope
- Translation

## Constraints
- AWS free tier

## How I’ll know it works
Captions lag under 2 seconds in a 5-minute test.
"""


def write(tmp_path, text, name="vision_test.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_parses_a_filled_brief(tmp_path):
    brief = parse_brief(write(tmp_path, GOOD))
    assert brief.title == "Live captions"
    assert brief.who_uses_it == "Me, a cloud technician."
    assert brief.must_haves == ["Captions within 2 seconds", "Works in Chrome"]
    assert brief.success_measure.startswith("Captions lag")


def test_blank_template_lists_every_problem(tmp_path):
    with pytest.raises(BriefError) as e:
        parse_brief(TEMPLATE)
    msg = str(e.value)
    assert "first line" in msg and "'who uses it' is empty" in msg and "'must-haves' is empty" in msg


def test_text_after_the_rule_is_ignored(tmp_path):
    brief = parse_brief(write(tmp_path, GOOD + "\n---\n## Must-haves\n- from the example\n"))
    assert "from the example" not in brief.must_haves


def test_more_than_five_must_haves_is_rejected(tmp_path):
    six = GOOD.replace("- Works in Chrome", "\n".join(f"- item {i}" for i in range(5)))
    with pytest.raises(BriefError, match="keep the 5"):
        parse_brief(write(tmp_path, six))


def test_missing_heading_is_named(tmp_path):
    with pytest.raises(BriefError, match="Constraints"):
        parse_brief(write(tmp_path, GOOD.replace("## Constraints", "## Limits")))


@pytest.fixture
def run_dir(tmp_path):
    card = CapabilityCard(
        card_id="C06", name="Live Spatial Speech Captions", what_it_does="Captions speech.",
        how_people_use_it="Automatic", strongest_evidence="observed",
        evidence=[EvidenceRef(segment_id="S015", at=330, what_happens="captions",
                              evidence="observed")],
        feasibility="buildable_now", feasibility_reason="ASR is mature.",
        real_world_analogues=[], hardware_needed=[], merged_from=["speech-to-text"],
    )
    (tmp_path / "cards.json").write_text(CapabilityCards(cards=[card], dropped=[]).model_dump_json())
    return tmp_path


def test_gate_writes_selection(run_dir):
    result = run_gate(run_dir, [("c06", write(run_dir, GOOD))])
    saved = GateResult.model_validate_json((run_dir / "gate.json").read_text())
    assert saved == result
    assert saved.selections[0].card.card_id == "C06"


def test_gate_reports_unknown_card_and_bad_brief_together(run_dir):
    bad = write(run_dir, "# Vision Brief: x\n", "vision_bad.md")
    with pytest.raises(BriefError) as e:
        run_gate(run_dir, [("C99", write(run_dir, GOOD)), ("C06", bad)])
    assert "C99" in str(e.value) and "vision_bad.md" in str(e.value)
    assert not (run_dir / "gate.json").exists()


def test_gate_rejects_more_than_three_picks(run_dir):
    brief = write(run_dir, GOOD)
    with pytest.raises(BriefError, match="1 to 3"):
        run_gate(run_dir, [("C06", brief)] * 4)
