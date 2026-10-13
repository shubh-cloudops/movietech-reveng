"""Architect tests with a fake Claude client: no API key, no cost."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from movietech.architect import architect, check
from movietech.costs import summarise
from movietech.schemas import (
    Adr,
    AdrOption,
    Architecture,
    CapabilityCard,
    Component,
    DataFlow,
    GateResult,
    GateSelection,
    VisionBrief,
)

MUST = ["Captions within 2 seconds", "Works in Chrome"]


def make_gate():
    card = CapabilityCard(
        card_id="C06", name="Live captions", what_it_does="Captions speech.",
        how_people_use_it="Automatic", strongest_evidence="observed", evidence=[],
        feasibility="buildable_now", feasibility_reason="ASR is mature.",
        real_world_analogues=[], hardware_needed=[], merged_from=["speech-to-text"],
    )
    brief = VisionBrief(title="Live captions", who_uses_it="Me", use_case="Meetings",
                        where_it_runs="Web", must_haves=MUST, out_of_scope=["Translation"],
                        constraints=["AWS free tier"], success_measure="Lag under 2 s")
    return GateResult(selections=[GateSelection(card=card, brief=brief, brief_file="v.md")])


def make_arch(must=MUST, flow_target="A2", options=2):
    opts = [AdrOption(option=f"opt{i}", pros=["p"], cons=["c"]) for i in range(options)]
    return Architecture(
        summary="Browser streams audio to a transcription service.",
        components=[
            Component(component_id="A1", name="Web client", responsibility="Captures audio",
                      technology="React", cloud_service="none", serves_cards=["C06"],
                      serves_must_haves=list(must)),
            Component(component_id="A2", name="Transcriber", responsibility="Speech to text",
                      technology="Amazon Transcribe streaming", cloud_service="AWS",
                      serves_cards=["C06"], serves_must_haves=[]),
        ],
        data_flows=[DataFlow(source="user", target="A1", data="speech", protocol="mic"),
                    DataFlow(source="A1", target=flow_target, data="audio", protocol="WebSocket")],
        adrs=[Adr(adr_id="ADR-001", title="ASR service", context="c", options=opts,
                  decision="Transcribe", consequences="AWS lock-in")],
        conflicts=[], assumptions=["Chrome only"], risks=[],
    )


class FakeClient:
    def __init__(self, arch):
        self.calls = []
        self.beta = SimpleNamespace(messages=self)
        self.message = SimpleNamespace(
            parsed_output=arch, stop_reason="end_turn", model="claude-opus-5-5",
            usage=SimpleNamespace(input_tokens=3_000, output_tokens=8_000,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0))

    @contextmanager
    def stream(self, **kwargs):
        self.calls.append(kwargs)
        yield SimpleNamespace(get_final_message=lambda: self.message)


@pytest.fixture
def run_dir(tmp_path):
    (tmp_path / "gate.json").write_text(make_gate().model_dump_json())
    return tmp_path


def test_writes_plan_and_sends_brief(run_dir):
    client = FakeClient(make_arch())
    _, problems = architect(run_dir, client=client)
    assert problems == []
    prompt = client.calls[0]["messages"][0]["content"]
    assert "Captions within 2 seconds" in prompt and "C06" in prompt
    assert client.calls[0]["output_format"] is Architecture
    md = (run_dir / "plan" / "architecture.md").read_text()
    assert "ADR-001" in md and "A1 → A2" in md
    # 3,000 * $4/M + 8,000 * $20/M = $0.172
    assert summarise(run_dir)["architect"]["usd"] == pytest.approx(0.172)


def test_check_finds_uncovered_must_have():
    problems = check(make_arch(must=["Works in Chrome"]), make_gate())
    assert problems == ["must-have not served by any component: Captions within 2 seconds"]


def test_check_finds_dangling_flow_and_one_option_adr():
    problems = check(make_arch(flow_target="A9", options=1), make_gate())
    assert any("A9" in p for p in problems)
    assert any("fewer than two options" in p for p in problems)


def test_needs_gate_first(tmp_path):
    with pytest.raises(FileNotFoundError, match="Run gate first"):
        architect(tmp_path, client=FakeClient(make_arch()))
