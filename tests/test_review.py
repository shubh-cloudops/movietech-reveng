"""War Room review round tests: fake Claude, fake Gemini, fake registries. No keys, no cost."""

import json
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from movietech import registries
from movietech.costs import BudgetExceeded, summarise
from movietech.review import (
    COST_OPS_RUBRIC,
    SKEPTIC_RUBRIC,
    gates,
    missing_checks,
    review,
)
from movietech.schemas import (
    CostReview,
    Dependency,
    DependencyList,
    Grounding,
    Objection,
    ObjectionDraft,
    Review,
    ReviewRound,
    RubricCheck,
)
from tests.test_architect import make_arch, make_gate
from tests.test_plan import make_plan, make_tests


def checks(rubric, failed=()):
    return [RubricCheck(item=i, passed=i not in failed, reason="r") for i in rubric]


DEPS = DependencyList(
    dependencies=[Dependency(ecosystem="npm", name="react", used_for="UI", component_ids=["A1"]),
                  Dependency(ecosystem="npm", name="made-up-pkg", used_for="?",
                             component_ids=["A2"])],
    not_checkable=["Amazon S3"])
COST = CostReview(objections=[], checks=checks(COST_OPS_RUBRIC), monthly_cost_usd=0.02,
                  cost_breakdown=["S3: 300 MB, $0.01"])
SKEPTIC = Review(objections=[ObjectionDraft(section="brief", severity="major",
                                            issue="'with accuracy' is not testable",
                                            evidence="must-have 2", proposed_fix="say 85%")],
                 checks=checks(SKEPTIC_RUBRIC))


class FakeClaude:
    """Answers by output_format, so the Grounder and Cost/Ops calls each get their own reply."""

    def __init__(self, outputs):
        self.outputs, self.calls = outputs, []
        self.beta = SimpleNamespace(messages=self)

    @contextmanager
    def stream(self, **kwargs):
        self.calls.append(kwargs)
        msg = SimpleNamespace(
            parsed_output=self.outputs[kwargs["output_format"]], stop_reason="end_turn",
            model=kwargs["model"],
            usage=SimpleNamespace(input_tokens=20_000, output_tokens=2_000,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0))
        yield SimpleNamespace(get_final_message=lambda: msg)


class FakeGemini:
    def __init__(self, parsed):
        self.models, self.parsed, self.calls = self, parsed, []

    def generate_content(self, model, contents, config):
        self.calls.append((contents, config))
        usage = SimpleNamespace(prompt_token_count=20_000, candidates_token_count=3_000,
                                thoughts_token_count=7_000)
        return SimpleNamespace(parsed=self.parsed, usage_metadata=usage)


NPM = {"https://registry.npmjs.org/react": {
    "dist-tags": {"latest": "19.2.0"}, "time": {"19.2.0": "2026-09-01T00:00:00Z"},
    "license": "MIT", "versions": {"19.2.0": {}}}}


@pytest.fixture
def run_dir(tmp_path):
    (tmp_path / "gate.json").write_text(make_gate().model_dump_json())
    plan = tmp_path / "plan"
    plan.mkdir()
    (plan / "architecture.json").write_text(make_arch().model_dump_json())
    (plan / "tasks.json").write_text(make_plan().model_dump_json())
    (plan / "tests.json").write_text(make_tests().model_dump_json())
    return tmp_path


def run(run_dir, skeptic=SKEPTIC, **kw):
    claude = FakeClaude({DependencyList: DEPS, CostReview: COST})
    gem = FakeGemini(skeptic)
    r, warnings = review(run_dir, claude=claude, gemini_client=gem, fetch=NPM.get, **kw)
    return r, warnings, claude, gem


def test_round_collects_all_three_reviewers(run_dir):
    r, warnings, claude, gem = run(run_dir)
    assert warnings == []
    assert [o.objection_id for o in r.objections] == ["SK101", "GR101"]
    gr = r.objections[1]
    assert gr.severity == "blocker" and "made-up-pkg" in gr.issue  # 404 on npm
    assert r.grounding[0].status == "ok" and r.grounding[0].latest_version == "19.2.0"
    assert r.monthly_cost_usd == 0.02
    # every reviewer reads the same plan, including the brief and the tests
    prompt = claude.calls[0]["messages"][0]["content"]
    assert "Captions within 2 seconds" in prompt and "ACCEPTANCE TESTS" in prompt
    assert gem.calls[0][0] == [prompt]
    # Haiku has no server-side fallback, so none is requested
    assert "fallbacks" not in claude.calls[0]


def test_files_and_debate_log(run_dir):
    run(run_dir)
    md = (run_dir / "plan" / "review_r1.md").read_text()
    assert "FAIL: 1 open blockers: GR101" in md and "made-up-pkg" in md
    log = [json.loads(line) for line in (run_dir / "debate_log.jsonl").read_text().splitlines()]
    assert [e["objection_id"] for e in log] == ["SK101", "GR101"]
    assert all(e["round"] == 1 and e["event"] == "raised" for e in log)
    r2, *_ = run(run_dir)
    assert r2.round == 2 and (run_dir / "plan" / "review_r2.json").exists()


def test_costs_are_logged_per_reviewer(run_dir):
    run(run_dir)
    totals = summarise(run_dir)
    # Haiku: 20,000 * $0.10/M + 2,000 * $0.50/M = $0.003 per call
    assert totals["review_grounder"]["usd"] == pytest.approx(0.003)
    # Gemini Pro: 20,000 * $2/M + (3,000 + 7,000) * $12/M = $0.16
    assert totals["review_skeptic"]["usd"] == pytest.approx(0.16)


def test_round_cap_is_shared(run_dir):
    with pytest.raises(BudgetExceeded):
        run(run_dir, max_usd=0.05)  # Skeptic's worst case alone is over $0.39


def test_skipped_rubric_item_is_reported(run_dir):
    lazy = SKEPTIC.model_copy(update={"checks": checks(SKEPTIC_RUBRIC[:-1])})
    _, warnings, *_ = run(run_dir, skeptic=lazy)
    assert warnings == ["skeptic skipped rubric item: logic and data flow"]


def test_gates():
    failed = gates(gates_round())
    assert failed[0] == "1 open blockers: GR01"
    assert failed[1].startswith("grounding 1/2 verified")
    assert "skeptic failed: security and privacy" in failed


def gates_round():
    r = ReviewRound(
        round=1,
        objections=[Objection(objection_id="GR01", reviewer="grounder", section="A2",
                              severity="blocker", issue="i", evidence="e", proposed_fix="f")],
        checks={"skeptic": checks(SKEPTIC_RUBRIC, failed=["security and privacy"])},
        grounding=[Grounding(ecosystem="npm", name="a", status="ok"),
                   Grounding(ecosystem="npm", name="b", status="missing")],
        not_checkable=[], monthly_cost_usd=0, cost_breakdown=[])
    return r


def test_missing_checks_ignores_case():
    done = [RubricCheck(item="Teardown", passed=True, reason="r")]
    assert "teardown" not in missing_checks(done, COST_OPS_RUBRIC)


@pytest.mark.parametrize("doc,status", [
    ({"dist-tags": {"latest": "1.0"}, "time": {"1.0": "2019-01-01"}, "license": "MIT",
      "versions": {"1.0": {}}}, "stale"),
    ({"dist-tags": {"latest": "1.0"}, "time": {"1.0": "2026-01-01"}, "license": "GPL-3.0",
      "versions": {"1.0": {}}}, "license"),
    ({"dist-tags": {"latest": "1.0"}, "time": {"1.0": "2026-01-01"}, "license": "MIT",
      "versions": {"1.0": {"deprecated": "use x"}}}, "archived"),
    ({"dist-tags": {"latest": "1.0"}, "time": {"1.0": "2026-01-01"},
      "license": {"type": "Apache-2.0"}, "versions": {"1.0": {}}}, "ok"),
])
def test_npm_judgement(doc, status):
    dep = Dependency(ecosystem="npm", name="x", used_for="u", component_ids=[])
    g = registries.ground(dep, fetch=lambda url: doc, today=date(2026, 10, 13))
    assert g.status == status


def test_pypi_licence_from_classifier():
    doc = {"info": {"version": "2.0", "license": "", "classifiers": [
        "License :: OSI Approved :: MIT License"]},
        "urls": [{"upload_time_iso_8601": "2026-05-01T00:00:00Z"}]}
    dep = Dependency(ecosystem="pypi", name="x", used_for="u", component_ids=[])
    g = registries.ground(dep, fetch=lambda url: doc, today=date(2026, 10, 13))
    assert (g.status, g.license, g.last_release) == ("ok", "MIT", "2026-05-01")


def test_lookup_error_is_not_a_crash():
    def broken(url):
        raise TimeoutError("slow")
    dep = Dependency(ecosystem="github", name="o/r", used_for="u", component_ids=[])
    assert registries.ground(dep, fetch=broken).status == "error"
