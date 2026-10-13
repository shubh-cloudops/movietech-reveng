"""Watcher Pass C tests with a fake Claude client: no API key, no cost."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from movietech.costs import BudgetExceeded, summarise
from movietech.schemas import (
    CapabilityCard,
    CapabilityCards,
    DeepLookResult,
    DroppedCapability,
    EvidenceRef,
    Observation,
)
from movietech.watcher_cards import make_cards


def card(card_id, merged_from, segment_id="S001"):
    return CapabilityCard(
        card_id=card_id, name="Face tracking HUD", what_it_does="Boxes faces in view.",
        how_people_use_it="Automatic", strongest_evidence="observed",
        evidence=[EvidenceRef(segment_id=segment_id, at=4.0, what_happens="box on face",
                              evidence="observed")],
        feasibility="buildable_now", feasibility_reason="Face detection is mature.",
        real_world_analogues=["OpenCV"], hardware_needed=[], merged_from=merged_from,
    )


class FakeClient:
    """Mimics client.beta.messages.stream(...) as a context manager and records the call."""

    def __init__(self, cards, stop_reason="end_turn", model="claude-opus-5-5"):
        self.calls = []
        self.beta = SimpleNamespace(messages=self)
        self.message = SimpleNamespace(
            parsed_output=cards, stop_reason=stop_reason, model=model,
            usage=SimpleNamespace(input_tokens=10_000, output_tokens=5_000,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0),
        )

    @contextmanager
    def stream(self, **kwargs):
        self.calls.append(kwargs)
        yield SimpleNamespace(get_final_message=lambda: self.message)


@pytest.fixture
def run_dir(tmp_path):
    obs = Observation(at=4.0, what_happens="box on face", interface="HUD",
                      user_input="automatic", evidence="observed")
    looks = [
        DeepLookResult(segment_id="S001", start=0, end=10, model="m", shows_software=True,
                       summary="HUD", observations=[obs], capabilities=["face tracking", "HUD"]),
        DeepLookResult(segment_id="S002", start=10, end=20, model="m", shows_software=False,
                       summary="credits", observations=[], capabilities=[]),
    ]
    (tmp_path / "deep.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in looks))
    return tmp_path


def test_writes_cards_and_sends_only_software_segments(run_dir):
    cards = CapabilityCards(cards=[card("C01", ["face tracking", "HUD"])], dropped=[])
    client = FakeClient(cards)
    result, warnings = make_cards(run_dir, client=client)
    assert warnings == []
    assert result.cards[0].card_id == "C01"
    prompt = client.calls[0]["messages"][0]["content"]
    assert "S001" in prompt and "S002" not in prompt
    assert client.calls[0]["output_format"] is CapabilityCards
    assert "C01: Face tracking HUD" in (run_dir / "cards.md").read_text()
    assert (run_dir / "cards.json").exists()


def test_warns_on_uncovered_capability_and_unknown_segment(run_dir):
    cards = CapabilityCards(cards=[card("C01", ["face tracking"], segment_id="S999")], dropped=[])
    _, warnings = make_cards(run_dir, client=FakeClient(cards))
    assert any("HUD" in w for w in warnings)
    assert any("S999" in w for w in warnings)


def test_dropped_capability_counts_as_covered(run_dir):
    cards = CapabilityCards(cards=[card("C01", ["face tracking"])],
                            dropped=[DroppedCapability(name="HUD", reason="too vague")])
    _, warnings = make_cards(run_dir, client=FakeClient(cards))
    assert warnings == []


def test_cost_is_logged(run_dir):
    cards = CapabilityCards(cards=[card("C01", ["face tracking", "HUD"])], dropped=[])
    make_cards(run_dir, client=FakeClient(cards))
    # 10,000 * $4/M + 5,000 * $20/M = $0.14
    assert summarise(run_dir)["cards"]["usd"] == pytest.approx(0.14)


def test_unknown_fallback_model_is_priced_high(run_dir):
    cards = CapabilityCards(cards=[card("C01", ["face tracking", "HUD"])], dropped=[])
    make_cards(run_dir, client=FakeClient(cards, model="some-other-model"))
    # 10,000 * $10/M + 5,000 * $50/M = $0.35
    assert summarise(run_dir)["cards"]["usd"] == pytest.approx(0.35)


def test_worst_case_over_cap_stops_before_calling(run_dir):
    client = FakeClient(None)
    with pytest.raises(BudgetExceeded):
        make_cards(run_dir, client=client, max_usd=0.10)
    assert client.calls == []


def test_truncated_answer_is_an_error(run_dir):
    with pytest.raises(RuntimeError, match="token limit"):
        make_cards(run_dir, client=FakeClient(None, stop_reason="max_tokens"))
