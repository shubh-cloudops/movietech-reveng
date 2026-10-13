"""Lead Developer: turns the architecture into milestones of small, ordered, verifiable tasks.

Reads <run_dir>/gate.json and <run_dir>/plan/architecture.json and writes
<run_dir>/plan/tasks.json and tasks.md. One Claude call, then a code check.
"""

from pathlib import Path

from movietech import config
from movietech.claude import structured_call
from movietech.schemas import Architecture, BuildPlan, GateResult

SYSTEM = """You are the Lead Developer in a pipeline that turns software seen in films into
real, buildable projects. You receive the user's Vision Briefs and the Architect's design.
Turn the design into a build plan a junior cloud engineer on Windows 11 can follow.

Rules:
- Follow the architecture. Do not add components, swap technologies or build anything listed
  under "Out of scope". Where the architecture lists open questions for the user, plan for
  what the Vision Brief says.
- Group tasks into milestones. Each milestone ends with something that runs and can be shown.
  Start with a local, free version; cloud deployment comes after it works locally.
- Each task is at most one day of work, builds named components, lists the files it touches,
  and has one observable done_when result.
- Every component is built by at least one task. Every must-have is delivered by at least one
  task: copy must-haves word for word.
- depends_on lists only earlier task ids; there are no cycles.
- Name any paid cloud resource a task creates with its estimated monthly cost. Prefer the
  free tier. Secrets go in a secret store or environment variables, never in code.
- The teardown removes every cloud resource the plan creates, in a safe order."""


def load_inputs(run_dir: Path) -> tuple[GateResult, Architecture]:
    """The briefs and the architecture, or a clear message saying which stage to run first."""
    gate_path = run_dir / "gate.json"
    arch_path = run_dir / "plan" / "architecture.json"
    if not gate_path.exists():
        raise FileNotFoundError(f"{gate_path} not found. Run gate first.")
    if not arch_path.exists():
        raise FileNotFoundError(f"{arch_path} not found. Run architect first.")
    gate = GateResult.model_validate_json(gate_path.read_text(encoding="utf-8"))
    arch = Architecture.model_validate_json(arch_path.read_text(encoding="utf-8"))
    return gate, arch


def briefs_text(gate: GateResult) -> str:
    return "\n".join(f"VISION BRIEF for {s.card.card_id} {s.brief.model_dump_json()}"
                     for s in gate.selections)


def all_tasks(plan: BuildPlan):
    return [t for m in plan.milestones for t in m.tasks]


def check(plan: BuildPlan, arch: Architecture, gate: GateResult) -> list[str]:
    """Problems a reviewer should not have to find by hand."""
    problems = []
    tasks = all_tasks(plan)
    ids = [t.task_id for t in tasks]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"task id used twice: {dup}")
    components = {c.component_id for c in arch.components}
    built = {c for t in tasks for c in t.component_ids}
    for c in sorted(components - built):
        problems.append(f"component not built by any task: {c}")
    for c in sorted(built - components):
        problems.append(f"task builds unknown component {c}")
    delivered = {m for t in tasks for m in t.must_haves}
    for s in gate.selections:
        for m in s.brief.must_haves:
            if m not in delivered:
                problems.append(f"must-have not delivered by any task: {m}")
    seen = set()
    for t in tasks:  # plan order: a dependency must come earlier, which also rules out cycles
        for d in t.depends_on:
            if d not in ids:
                problems.append(f"{t.task_id} depends on unknown task {d}")
            elif d not in seen:
                problems.append(f"{t.task_id} depends on {d}, which comes later in the plan")
        seen.add(t.task_id)
    if not plan.teardown:
        problems.append("no teardown steps")
    return problems


def to_markdown(plan: BuildPlan) -> str:
    lines = ["# Build plan", "", plan.summary, "", "## Repo layout", "", "```"]
    lines += plan.repo_layout + ["```", ""]
    for m in plan.milestones:
        lines += [f"## {m.milestone_id}: {m.title}", "", f"**Goal:** {m.goal}", ""]
        for t in m.tasks:
            after = ", ".join(t.depends_on) or "nothing"
            lines += [f"### {t.task_id}: {t.title} ({t.size})", "", t.description, "",
                      f"- **Builds:** {', '.join(t.component_ids) or 'setup'}",
                      f"- **After:** {after}",
                      f"- **Files:** {', '.join(t.files)}",
                      f"- **Done when:** {t.done_when}"]
            if t.must_haves:
                lines.append(f"- **Must-haves:** {'; '.join(t.must_haves)}")
            if t.paid_resource != "none":
                lines.append(f"- **Paid resource:** {t.paid_resource}")
            lines.append("")
    lines += ["## Teardown", ""] + [f"{i}. {s}" for i, s in enumerate(plan.teardown, 1)]
    return "\n".join(lines) + "\n"


def lead_dev(run_dir: Path, client=None, model: str = config.LEAD_DEV_MODEL,
             max_usd: float = config.LEAD_DEV_MAX_RUN_USD) -> tuple[BuildPlan, list[str]]:
    """Run the Lead Developer. Returns the plan and any problems the code check found."""
    gate, arch = load_inputs(run_dir)
    plan = structured_call(
        client=client, model=model, system=SYSTEM,
        user=f"{briefs_text(gate)}\n\nARCHITECTURE {arch.model_dump_json()}",
        output_format=BuildPlan, effort=config.LEAD_DEV_EFFORT,
        max_tokens=config.LEAD_DEV_MAX_TOKENS, max_usd=max_usd, stage="lead_dev",
        run_dir=run_dir, cap_env="MOVIETECH_LEAD_DEV_MAX_USD",
    )
    problems = check(plan, arch, gate)
    (run_dir / "plan" / "tasks.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "plan" / "tasks.md").write_text(to_markdown(plan), encoding="utf-8")
    return plan, problems
