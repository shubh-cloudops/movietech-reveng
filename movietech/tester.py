"""Test Engineer: writes the acceptance tests that prove each task and must-have works.

A separate call from the Lead Developer, so the tests are not written by the plan's author.
Reads gate.json, plan/architecture.json and plan/tasks.json; writes plan/tests.json and
tests.md. One Claude call, then a code check.
"""

from pathlib import Path

from movietech import config
from movietech.claude import structured_call
from movietech.lead_dev import all_tasks, briefs_text, load_inputs
from movietech.schemas import AcceptancePlan, Architecture, BuildPlan, GateResult

SYSTEM = """You are the Test Engineer in a pipeline that turns software seen in films into
real, buildable projects. You receive the user's Vision Briefs, the architecture and the Lead
Developer's build plan. Write the acceptance tests that prove the plan delivers what the user
asked for.

Rules:
- Every task is verified by at least one test. Every must-have is proved by at least one
  test: copy must-haves word for word. At least one test proves each brief's "how I'll know
  it works" and sets checks_success_measure to true.
- "then" is measurable: a number, a visible result or an exit code, never "works well".
- Prefer automated tests with the tools the architecture already uses. Use manual tests only
  where automation is not practical (for example, judging captions by eye), and write their
  steps so anyone can repeat them.
- how_to_run gives an exact command for automated tests. Commands run in PowerShell on
  Windows 11 or in GitHub Actions on Ubuntu.
- Tests need no real secrets: use fakes, local emulators or test fixtures.
- ci_steps run every automated test in GitHub Actions on each pull request."""


def check(tests: AcceptancePlan, plan: BuildPlan, gate: GateResult) -> list[str]:
    """Problems a reviewer should not have to find by hand."""
    problems = []
    ids = [t.test_id for t in tests.tests]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"test id used twice: {dup}")
    task_ids = {t.task_id for t in all_tasks(plan)}
    tested = {i for t in tests.tests for i in t.task_ids}
    for i in sorted(task_ids - tested):
        problems.append(f"task not verified by any test: {i}")
    for i in sorted(tested - task_ids):
        problems.append(f"test refers to unknown task {i}")
    proved = {m for t in tests.tests for m in t.must_haves}
    for s in gate.selections:
        for m in s.brief.must_haves:
            if m not in proved:
                problems.append(f"must-have not proved by any test: {m}")
    if not any(t.checks_success_measure for t in tests.tests):
        problems.append("no test checks the brief's success measure")
    if not tests.ci_steps:
        problems.append("no CI steps")
    return problems


def to_markdown(tests: AcceptancePlan) -> str:
    lines = ["# Acceptance tests", "", tests.strategy, "",
             "| Id | Kind | Tool | Verifies | Then |", "|---|---|---|---|---|"]
    for t in tests.tests:
        lines.append(f"| {t.test_id} | {t.kind.replace('_', ' ')} | {t.tool} | "
                     f"{', '.join(t.task_ids)} | {t.then} |")
    lines.append("")
    for t in tests.tests:
        lines += [f"## {t.test_id}", "", f"- **Given** {t.given}", f"- **When** {t.when}",
                  f"- **Then** {t.then}", f"- **Run:** {t.how_to_run}"]
        if t.must_haves:
            lines.append(f"- **Proves:** {'; '.join(t.must_haves)}")
        if t.checks_success_measure:
            lines.append("- **Proves the success measure**")
        lines.append("")
    lines += ["## CI steps", ""] + [f"{i}. {s}" for i, s in enumerate(tests.ci_steps, 1)]
    return "\n".join(lines) + "\n"


def _load_plan(run_dir: Path) -> BuildPlan:
    path = run_dir / "plan" / "tasks.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run tasks first.")
    return BuildPlan.model_validate_json(path.read_text(encoding="utf-8"))


def write_tests(run_dir: Path, client=None, model: str = config.TEST_ENG_MODEL,
             max_usd: float = config.TEST_ENG_MAX_RUN_USD) -> tuple[AcceptancePlan, list[str]]:
    """Run the Test Engineer. Returns the tests and any problems the code check found."""
    gate, arch = load_inputs(run_dir)
    plan = _load_plan(run_dir)
    tests = structured_call(
        client=client, model=model, system=SYSTEM,
        user=(f"{briefs_text(gate)}\n\nARCHITECTURE {_arch_brief(arch)}"
              f"\n\nBUILD PLAN {plan.model_dump_json()}"),
        output_format=AcceptancePlan, effort=config.TEST_ENG_EFFORT,
        max_tokens=config.TEST_ENG_MAX_TOKENS, max_usd=max_usd, stage="test_eng",
        run_dir=run_dir, cap_env="MOVIETECH_TEST_ENG_MAX_USD",
    )
    problems = check(tests, plan, gate)
    (run_dir / "plan" / "tests.json").write_text(tests.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "plan" / "tests.md").write_text(to_markdown(tests), encoding="utf-8")
    return tests, problems


def _arch_brief(arch: Architecture) -> str:
    """The parts of the architecture a tester needs: components and data flows, not the ADRs."""
    return arch.model_dump_json(include={"summary", "components", "data_flows", "conflicts"})
