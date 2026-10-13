"""War Room loop tests: fake Claude, fake Gemini, fake registries. No keys, no cost."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from movietech import chairman
from movietech.review import COST_OPS_RUBRIC, SKEPTIC_RUBRIC
from movietech.schemas import (
    AcceptancePlan,
    Architecture,
    BuildPlan,
    CostReview,
    DependencyList,
    LedgerEntry,
    Objection,
    ObjectionDraft,
    Review,
    RubricCheck,
    Ruling,
    Triage,
    Verdict,
)
from movietech.warroom import approve, warroom
from tests.test_architect import make_arch, make_gate
from tests.test_plan import make_plan, make_tests


def checks(rubric):
    return [RubricCheck(item=i, passed=True, reason="r") for i in rubric]


BLOCKER = ObjectionDraft(section="A2", severity="blocker", issue="no offline mode",
                         evidence="brief says offline", proposed_fix="run Whisper in browser")
COST = CostReview(objections=[], checks=checks(COST_OPS_RUBRIC), monthly_cost_usd=0.01,
                  cost_breakdown=[])


def skeptic(objections=(), verdicts=()):
    return Review(objections=list(objections), checks=checks(SKEPTIC_RUBRIC),
                  verdicts=list(verdicts))


class FakeClaude:
    """Answers by output_format; records every call so tests can see who was asked what."""

    def __init__(self, outputs):
        self.outputs, self.calls = outputs, []
        self.beta = SimpleNamespace(messages=self)

    @contextmanager
    def stream(self, **kwargs):
        self.calls.append(kwargs)
        out = self.outputs[kwargs["output_format"]]
        msg = SimpleNamespace(
            parsed_output=out() if callable(out) else out, stop_reason="end_turn",
            model=kwargs["model"],
            usage=SimpleNamespace(input_tokens=10_000, output_tokens=1_000,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0))
        yield SimpleNamespace(get_final_message=lambda: msg)

    def stages(self):
        return [c["output_format"].__name__ for c in self.calls]


class FakeGemini:
    """Returns the next Skeptic review in the list on each call."""

    def __init__(self, reviews):
        self.models, self.reviews, self.prompts = self, list(reviews), []

    def generate_content(self, model, contents, config):
        self.prompts.append(contents[0])
        usage = SimpleNamespace(prompt_token_count=10_000, candidates_token_count=1_000,
                                thoughts_token_count=1_000)
        return SimpleNamespace(parsed=self.reviews.pop(0), usage_metadata=usage)


def claude(decision="accept", owner="architect"):
    fixed = make_arch()
    fixed.summary = "Revised: runs offline."
    return FakeClaude({
        DependencyList: DependencyList(dependencies=[], not_checkable=[]),
        CostReview: COST,
        Triage: lambda: Triage(rulings=[Ruling(objection_id="SK101", decision=decision,
                                               owner=owner, reason="real gap",
                                               question_for_user="Offline?")]),
        Architecture: fixed, BuildPlan: make_plan(), AcceptancePlan: make_tests(),
    })


@pytest.fixture
def run_dir(tmp_path):
    (tmp_path / "gate.json").write_text(make_gate().model_dump_json())
    plan = tmp_path / "plan"
    plan.mkdir()
    (plan / "architecture.json").write_text(make_arch().model_dump_json())
    (plan / "tasks.json").write_text(make_plan().model_dump_json())
    (plan / "tests.json").write_text(make_tests().model_dump_json())
    return tmp_path


def test_accepted_blocker_is_fixed_then_closed_by_its_raiser(run_dir):
    c = claude()
    gem = FakeGemini([skeptic([BLOCKER]),
                      skeptic(verdicts=[Verdict(objection_id="SK101", closed=True,
                                                reason="now offline")])])
    r, failed, stopped = warroom(run_dir, claude=c, gemini_client=gem, fetch=lambda u: None)
    assert (r.round, failed) == (2, [])
    assert stopped.startswith("All gates pass")
    # round 1: reviewers, Chairman, then only the architect revises (nothing else was routed)
    assert c.stages() == ["DependencyList", "CostReview", "Triage", "Architecture",
                          "DependencyList", "CostReview"]
    revise_prompt = c.calls[3]["messages"][0]["content"]
    assert "SK101" in revise_prompt and "no offline mode" in revise_prompt
    assert "Revised: runs offline." in (run_dir / "plan" / "architecture.md").read_text()
    # round 2: the Skeptic re-checks its own objection against the revised plan
    assert "EARLIER OBJECTIONS YOU RAISED" in gem.prompts[1] and "SK101" in gem.prompts[1]
    ledger = chairman.load_ledger(run_dir)
    assert ledger["SK101"].status == "closed"
    assert (run_dir / "plan" / "history" / "v1" / "architecture.json").exists()
    events = [json.loads(x)["event"]
              for x in (run_dir / "debate_log.jsonl").read_text().splitlines()]
    assert events == ["raised", "accept", "revised", "closed"]
    assert "Fixed and closed" in (run_dir / "plan" / "warroom.md").read_text()


def test_rejected_objection_stops_without_another_paid_round(run_dir):
    c = claude(decision="reject")
    gem = FakeGemini([skeptic([BLOCKER])])
    r, failed, stopped = warroom(run_dir, claude=c, gemini_client=gem, fetch=lambda u: None)
    assert r.round == 1 and failed and "need your decision" in stopped
    assert "Architecture" not in c.stages()  # nobody revises a rejected objection
    assert chairman.load_ledger(run_dir)["SK101"].status == "rejected"
    assert "Rejected by the Chairman" in (run_dir / "plan" / "warroom.md").read_text()


def test_ask_user_lands_in_questions(run_dir):
    c = claude(decision="ask_user", owner="none")
    warroom(run_dir, claude=c, gemini_client=FakeGemini([skeptic([BLOCKER])]),
            fetch=lambda u: None)
    md = (run_dir / "plan" / "warroom.md").read_text()
    assert "## Questions for you" in md and "SK101: Offline?" in md


def test_round_cap(run_dir):
    c = claude()
    gem = FakeGemini([skeptic([BLOCKER]), skeptic(), skeptic()])  # never closes SK101
    r, failed, stopped = warroom(run_dir, claude=c, gemini_client=gem, fetch=lambda u: None)
    assert r.round == 3 and "1 open blockers: SK101" in failed[0]
    assert "3-round cap" in stopped
    assert c.stages().count("Architecture") == 2  # revised after rounds 1 and 2, not 3


def test_only_the_raiser_can_close(run_dir):
    c = claude()
    sneaky = COST.model_copy(update={"verdicts": [Verdict(objection_id="SK101", closed=True,
                                                          reason="trust me")]})
    c.outputs[CostReview] = sneaky
    gem = FakeGemini([skeptic([BLOCKER]), skeptic()])
    warroom(run_dir, claude=c, gemini_client=gem, fetch=lambda u: None, max_rounds=2)
    assert chairman.load_ledger(run_dir)["SK101"].status == "open"


def test_resume_does_not_repeat_finished_steps(run_dir):
    c = claude()
    gem = FakeGemini([skeptic([BLOCKER]), skeptic()])
    warroom(run_dir, claude=c, gemini_client=gem, fetch=lambda u: None, max_rounds=2)
    again = claude()
    r, *_ = warroom(run_dir, claude=again, gemini_client=FakeGemini([]), fetch=lambda u: None,
                    max_rounds=2)
    assert r.round == 2 and again.calls == []


def test_approve_writes_plan_and_record(run_dir):
    c = claude(decision="reject")
    warroom(run_dir, claude=c, gemini_client=FakeGemini([skeptic([BLOCKER])]),
            fetch=lambda u: None)
    path = approve(run_dir, notes="Dry run, fine for now")
    text = path.read_text()
    assert text.startswith("# Project plan (approved)") and "Dry run" in text
    assert "# Architecture" in text and "# Build plan" in text and "# Acceptance tests" in text
    record = json.loads((run_dir / "plan" / "approval.json").read_text())
    assert record["approved"] and record["round"] == 1


def test_approve_needs_warroom_first(run_dir):
    with pytest.raises(FileNotFoundError, match="Run warroom first"):
        approve(run_dir)


def test_missing_ruling_is_accepted_and_routed_by_section():
    obj = Objection(objection_id="CO101", reviewer="cost_ops", section="T05",
                    severity="major", issue="i", evidence="e", proposed_fix="f")
    ledger = {"CO101": LedgerEntry(objection=obj, round_raised=1)}
    warnings = chairman.apply_rulings(ledger, [])
    assert warnings == ["no ruling for CO101; accepted and routed by its section"]
    assert (ledger["CO101"].status, ledger["CO101"].owner) == ("open", "lead_dev")


@pytest.mark.parametrize("section,owner", [("A3", "architect"), ("ADR-002", "architect"),
                                           ("T07", "lead_dev"), ("AT05", "test_eng"),
                                           ("architecture", "architect"), ("tests", "test_eng")])
def test_default_owner(section, owner):
    assert chairman.default_owner(section) == owner
