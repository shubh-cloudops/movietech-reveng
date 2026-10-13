"""Lead Developer and Test Engineer tests with a fake Claude client: no API key, no cost."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from movietech import lead_dev, tester
from movietech.costs import summarise
from movietech.schemas import (
    AcceptancePlan,
    AcceptanceTest,
    BuildPlan,
    GateResult,
    GateSelection,
    Milestone,
    Task,
)
from tests.test_architect import MUST, make_arch, make_gate


def task(task_id, components=("A1",), must=(), after=()):
    return Task(task_id=task_id, title="t", description="d", component_ids=list(components),
                must_haves=list(must), depends_on=list(after), files=["src/x.ts"],
                done_when="it runs", size="S", paid_resource="none")


def make_plan(tasks=None, teardown=("Delete the stack",)):
    tasks = tasks or [task("T01", ["A1"], must=MUST), task("T02", ["A2"], after=["T01"])]
    return BuildPlan(summary="Local first.", repo_layout=["src/  app"],
                     milestones=[Milestone(milestone_id="M1", title="Local", goal="Runs",
                                           tasks=tasks)],
                     teardown=list(teardown))


def acc(test_id, task_ids=("T01", "T02"), must=MUST, success=True):
    return AcceptanceTest(test_id=test_id, task_ids=list(task_ids), must_haves=list(must),
                          checks_success_measure=success, kind="end_to_end", given="g",
                          when="w", then="caption within 2 s", how_to_run="npx playwright test",
                          tool="Playwright")


def make_tests(tests=None, ci=("npm test",)):
    return AcceptancePlan(strategy="Unit then e2e.", tests=tests or [acc("AT01")],
                          ci_steps=list(ci))


class FakeClient:
    def __init__(self, output):
        self.calls = []
        self.beta = SimpleNamespace(messages=self)
        self.message = SimpleNamespace(
            parsed_output=output, stop_reason="end_turn", model="claude-opus-5-5",
            usage=SimpleNamespace(input_tokens=5_000, output_tokens=10_000,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0))

    @contextmanager
    def stream(self, **kwargs):
        self.calls.append(kwargs)
        yield SimpleNamespace(get_final_message=lambda: self.message)


@pytest.fixture
def run_dir(tmp_path):
    (tmp_path / "gate.json").write_text(make_gate().model_dump_json())
    (tmp_path / "plan").mkdir()
    (tmp_path / "plan" / "architecture.json").write_text(make_arch().model_dump_json())
    return tmp_path


def test_lead_dev_writes_plan_from_brief_and_architecture(run_dir):
    client = FakeClient(make_plan())
    _, problems = lead_dev.lead_dev(run_dir, client=client)
    assert problems == []
    prompt = client.calls[0]["messages"][0]["content"]
    assert "Captions within 2 seconds" in prompt and "Amazon Transcribe" in prompt
    assert client.calls[0]["output_format"] is BuildPlan
    md = (run_dir / "plan" / "tasks.md").read_text()
    assert "### T02" in md and "**After:** T01" in md and "1. Delete the stack" in md
    # 5,000 * $4/M + 10,000 * $20/M = $0.22
    assert summarise(run_dir)["lead_dev"]["usd"] == pytest.approx(0.22)


def test_lead_dev_needs_architect_first(tmp_path):
    (tmp_path / "gate.json").write_text(make_gate().model_dump_json())
    with pytest.raises(FileNotFoundError, match="Run architect first"):
        lead_dev.lead_dev(tmp_path, client=FakeClient(make_plan()))


def test_plan_check_finds_gaps():
    plan = make_plan(tasks=[task("T01", ["A1", "A9"], must=["Works in Chrome"], after=["T02"]),
                            task("T02", ["A1"], after=["T07"])], teardown=())
    problems = lead_dev.check(plan, make_arch(), make_gate())
    assert "component not built by any task: A2" in problems
    assert "task builds unknown component A9" in problems
    assert "must-have not delivered by any task: Captions within 2 seconds" in problems
    assert "T01 depends on T02, which comes later in the plan" in problems
    assert "T02 depends on unknown task T07" in problems
    assert "no teardown steps" in problems


def test_plan_check_finds_duplicate_task_id():
    plan = make_plan(tasks=[task("T01", ["A1"], must=MUST), task("T01", ["A2"])])
    assert "task id used twice: T01" in lead_dev.check(plan, make_arch(), make_gate())


def test_tester_writes_tests_from_plan(run_dir):
    (run_dir / "plan" / "tasks.json").write_text(make_plan().model_dump_json())
    client = FakeClient(make_tests())
    _, problems = tester.write_tests(run_dir, client=client)
    assert problems == []
    prompt = client.calls[0]["messages"][0]["content"]
    assert "BUILD PLAN" in prompt and "T02" in prompt
    assert "ADR-001" not in prompt  # testers get components and flows, not the ADRs
    assert client.calls[0]["output_format"] is AcceptancePlan
    md = (run_dir / "plan" / "tests.md").read_text()
    assert "## AT01" in md and "Proves the success measure" in md
    assert summarise(run_dir)["test_eng"]["calls"] == 1


def test_tester_needs_tasks_first(run_dir):
    with pytest.raises(FileNotFoundError, match="Run tasks first"):
        tester.write_tests(run_dir, client=FakeClient(make_tests()))


def test_tests_check_finds_gaps():
    tests = make_tests(tests=[acc("AT01", ["T01", "T09"], must=["Works in Chrome"],
                                  success=False), acc("AT01", ["T01"], must=[], success=False)],
                       ci=())
    problems = tester.check(tests, make_plan(), make_gate())
    assert "test id used twice: AT01" in problems
    assert "task not verified by any test: T02" in problems
    assert "test refers to unknown task T09" in problems
    assert "must-have not proved by any test: Captions within 2 seconds" in problems
    assert "no test checks the brief's success measure" in problems
    assert "no CI steps" in problems


def test_must_haves_from_every_brief_are_checked():
    gate = make_gate()
    second = gate.selections[0].brief.model_copy(update={"must_haves": ["Offline mode"]})
    gate = GateResult(selections=[*gate.selections,
                                  GateSelection(card=gate.selections[0].card.model_copy(
                                      update={"card_id": "C07"}), brief=second,
                                      brief_file="w.md")])
    assert "must-have not delivered by any task: Offline mode" in lead_dev.check(
        make_plan(), make_arch(), gate)
    assert "must-have not proved by any test: Offline mode" in tester.check(
        make_tests(), make_plan(), gate)
