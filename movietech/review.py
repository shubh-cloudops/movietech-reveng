"""War Room review round: three independent reviewers check the plan and raise objections.

- Skeptic (Gemini, a different model family from the Claude authors): red-teams the plan
  against the Vision Brief and the film's evidence.
- Grounder (Haiku names the dependencies; code checks them on npm, PyPI and GitHub).
- Cost/Ops Reviewer (Haiku): monthly cost, free-tier fit, teardown, secrets, observability.

From round 2 on, each reviewer also re-checks the objections it raised earlier that the
Chairman accepted: only the reviewer who raised an objection can close it.

Reads gate.json and plan/architecture.json, tasks.json, tests.json. Writes
plan/review_r<N>.json and review_r<N>.md, and appends every objection to debate_log.jsonl.
"""

import json
from pathlib import Path

from movietech import config, gemini
from movietech.claude import structured_call
from movietech.costs import summarise
from movietech.lead_dev import load_inputs
from movietech.registries import ground
from movietech.schemas import (
    AcceptancePlan,
    BuildPlan,
    CostReview,
    DependencyList,
    GateResult,
    Objection,
    Review,
    ReviewRound,
    RubricCheck,
    Verdict,
)

SKEPTIC_RUBRIC = ["vision fit: every must-have", "vision fit: nothing out of scope",
                  "film coverage", "feasibility", "security and privacy", "testability",
                  "logic and data flow"]
COST_OPS_RUBRIC = ["monthly cost within the brief's budget", "free tier fit", "teardown",
                   "secrets handling", "observability"]

SKEPTIC = """You are the Skeptic in a War Room that reviews software plans built from films.
A different AI wrote the plan. Your job is to find what is wrong with it before anyone builds it.

Check the plan against the user's Vision Brief first, then the film's capability card. Raise an
objection for each real problem, citing the exact id (A3, T07, AT05, ADR-002) and the evidence.
- blocker: a must-have is not delivered, something out of scope is built, or a part cannot work.
- major: a real gap: an unrealistic claim, a security or privacy hole, an untestable must-have,
  a missing step.
- minor: an improvement.
Vague wording in the brief that makes a must-have impossible to test is itself an objection
(section 'brief'); propose a measurable rewording for the user.
Do not raise style preferences. Do not invent problems to look thorough.
Then certify every rubric item below as passed or failed with a one-line reason:
{rubric}"""

COST_OPS = """You are the Cost/Ops Reviewer in a War Room that reviews software plans.
Estimate what the plan costs per month once deployed, after free tiers, for one user. List each
resource with the usage you assumed. Raise an objection (citing the task or component id) for:
cost over the brief's budget; a paid resource the plan does not flag; a resource the teardown
misses; a secret that could end up in code; no way to see errors or spend in production.
Then certify every rubric item below as passed or failed with a one-line reason:
{rubric}"""

GROUNDER = """List every third-party package or open-source repository this plan names or
clearly needs, with its exact public registry name: npm for JavaScript/TypeScript, PyPI for
Python, or 'owner/repo' on GitHub for code that is not on a package registry. Do not guess a
name you are unsure of; give your best exact name and the registry check will catch mistakes.
Put managed cloud services (e.g. Amazon S3), browser APIs (e.g. Web Audio API) and models
hosted on a model hub in not_checkable."""

RECHECK = """

EARLIER OBJECTIONS YOU RAISED. The authors have revised the plan since. For each one, return a
verdict: closed (true) only if the plan above now fixes it, with a one-line reason. Do not raise
these again as new objections.
{objections}"""

GROUNDING_SEVERITY = {"missing": "blocker", "archived": "blocker", "stale": "major",
                      "license": "major", "error": "minor"}


def _load(run_dir: Path):
    gate, arch = load_inputs(run_dir)
    plan_dir = run_dir / "plan"
    for name, stage in (("tasks.json", "tasks"), ("tests.json", "tests")):
        if not (plan_dir / name).exists():
            raise FileNotFoundError(f"{plan_dir / name} not found. Run {stage} first.")
    tasks = BuildPlan.model_validate_json((plan_dir / "tasks.json").read_text(encoding="utf-8"))
    tests = AcceptancePlan.model_validate_json(
        (plan_dir / "tests.json").read_text(encoding="utf-8"))
    return gate, arch, tasks, tests


def plan_text(gate: GateResult, arch, tasks, tests) -> str:
    """The whole plan in one stable order, so every reviewer reads the same thing."""
    cards = "\n".join(f"CARD {s.card.model_dump_json(exclude={'merged_from'})}\n"
                      f"VISION BRIEF {s.brief.model_dump_json()}" for s in gate.selections)
    return (f"{cards}\n\nARCHITECTURE {arch.model_dump_json()}\n\n"
            f"BUILD PLAN {tasks.model_dump_json()}\n\nACCEPTANCE TESTS {tests.model_dump_json()}")


def _number(drafts: list[dict], reviewer: str, prefix: str, rnd: int) -> list[Objection]:
    """Ids carry the round, so they stay unique across rounds: SK101 is round 1's first."""
    return [Objection(objection_id=f"{prefix}{rnd}{i:02d}", reviewer=reviewer, **d)
            for i, d in enumerate(drafts, 1)]


def _recheck(open_before: list[Objection], reviewer: str) -> str:
    mine = [o.model_dump() for o in open_before if o.reviewer == reviewer]
    return RECHECK.format(objections=json.dumps(mine)) if mine else ""


def missing_checks(checks: list[RubricCheck], rubric: list[str]) -> list[str]:
    """Rubric items a reviewer skipped: it must certify each one, not just say 'looks good'."""
    done = {c.item.strip().lower() for c in checks}
    return [item for item in rubric if item.lower() not in done]


def gates(r: ReviewRound) -> list[str]:
    """Hard gates, checked by code. The plan passes the round only when this list is empty."""
    failed = []
    blockers = [o.objection_id for o in r.carried + r.objections if o.severity == "blocker"]
    if blockers:
        failed.append(f"{len(blockers)} open blockers: {', '.join(blockers)}")
    bad = [g.name for g in r.grounding if g.status != "ok"]
    if bad:
        failed.append(f"grounding {len(r.grounding) - len(bad)}/{len(r.grounding)} verified; "
                      f"not verified: {', '.join(bad)}")
    for reviewer, checks in r.checks.items():
        failed += [f"{reviewer} failed: {c.item}" for c in checks if not c.passed]
    return failed


def to_markdown(r: ReviewRound) -> str:
    lines = [f"# War Room review, round {r.round}", "", "## Gates", ""]
    lines += [f"- FAIL: {g}" for g in gates(r)] or ["- All gates pass."]
    lines += ["", "## Objections", "", "| Id | Severity | Section | Issue | Proposed fix |",
              "|---|---|---|---|---|"]
    order = {"blocker": 0, "major": 1, "minor": 2}
    for o in sorted(r.objections, key=lambda o: order[o.severity]):
        lines.append(f"| {o.objection_id} | {o.severity} | {o.section} | {o.issue} "
                     f"*Evidence:* {o.evidence} | {o.proposed_fix} |")
    if r.verdicts or r.carried:
        lines += ["", "## Earlier objections, re-checked by the reviewer who raised them", ""]
        lines += [f"- {v.objection_id}: {'closed' if v.closed else 'still open'}. {v.reason}"
                  for v in r.verdicts]
        lines += [f"- {o.objection_id} ({o.severity}) still open: {o.issue}" for o in r.carried
                  if o.objection_id not in {v.objection_id for v in r.verdicts}]
    lines += ["", "## Rubric", ""]
    for reviewer, checks in r.checks.items():
        lines += [f"- **{reviewer}** {'PASS' if c.passed else 'FAIL'} {c.item}: {c.reason}"
                  for c in checks]
    lines += ["", "## Grounding (checked on the live registries)", "",
              "| Registry | Name | Status | Latest | Last release | Licence | Note |",
              "|---|---|---|---|---|---|---|"]
    lines += [f"| {g.ecosystem} | {g.name} | {g.status} | {g.latest_version} | "
              f"{g.last_release} | {g.license} | {g.note} |" for g in r.grounding]
    if r.not_checkable:
        lines += ["", f"Not checkable on a registry: {', '.join(r.not_checkable)}"]
    lines += ["", f"## Cost (estimate): ${r.monthly_cost_usd:.2f} per month", ""]
    lines += [f"- {c}" for c in r.cost_breakdown]
    return "\n".join(lines) + "\n"


def _spent(run_dir: Path) -> float:
    return sum(t["usd"] for s, t in summarise(run_dir).items() if s.startswith("review_"))


def review(run_dir: Path, claude=None, gemini_client=None, fetch=None,
           max_usd: float = config.REVIEW_MAX_RUN_USD,
           open_before: list[Objection] = ()) -> tuple[ReviewRound, list[str]]:
    """Run one review round. `open_before` are earlier accepted objections still open; their
    raisers re-check them. Returns the round and any reviewer that skipped rubric items."""
    gate, arch, tasks, tests = _load(run_dir)
    plan = plan_text(gate, arch, tasks, tests)
    plan_dir = run_dir / "plan"
    rnd = len(list(plan_dir.glob("review_r*.json"))) + 1
    start = _spent(run_dir)

    def left() -> float:  # one cap for the whole round, shared by the three reviewers
        return max_usd - (_spent(run_dir) - start)

    deps = structured_call(
        client=claude, model=config.GROUNDER_MODEL, system=GROUNDER, user=plan,
        output_format=DependencyList, effort="medium", max_tokens=config.REVIEW_MAX_TOKENS,
        max_usd=left(), stage="review_grounder", run_dir=run_dir,
        cap_env="MOVIETECH_REVIEW_MAX_USD")
    lookups = [ground(d) if fetch is None else ground(d, fetch=fetch) for d in deps.dependencies]
    flagged = {g.name for g in lookups if g.status != "ok"}
    verdicts = [Verdict(objection_id=o.objection_id,
                        closed=not any(f" {n}:" in o.issue for n in flagged),
                        reason="registry re-check")
                for o in open_before if o.reviewer == "grounder"]
    grounder_drafts = [
        {"section": "architecture", "severity": GROUNDING_SEVERITY[g.status],
         "issue": f"{g.ecosystem} package {g.name}: {g.note}",
         "evidence": f"registry lookup: status {g.status}, latest {g.latest_version or '-'}, "
                     f"last release {g.last_release or '-'}, licence {g.license or '-'}",
         "proposed_fix": "replace it with a maintained, permissively licensed alternative"
                         if g.status != "error" else "check it by hand and rerun"}
        for g in lookups if g.status != "ok"]

    skeptic = gemini.structured_call(
        client=gemini_client, model=config.SKEPTIC_MODEL,
        system=SKEPTIC.format(rubric="\n".join(f"- {i}" for i in SKEPTIC_RUBRIC)),
        user=plan + _recheck(open_before, "skeptic"),
        schema=Review, thinking=config.SKEPTIC_THINKING, max_tokens=config.SKEPTIC_MAX_TOKENS,
        max_usd=left(), stage="review_skeptic", run_dir=run_dir,
        cap_env="MOVIETECH_REVIEW_MAX_USD")
    cost = structured_call(
        client=claude, model=config.COST_OPS_MODEL,
        system=COST_OPS.format(rubric="\n".join(f"- {i}" for i in COST_OPS_RUBRIC)),
        user=plan + _recheck(open_before, "cost_ops"),
        output_format=CostReview, effort=config.REVIEW_EFFORT,
        max_tokens=config.REVIEW_MAX_TOKENS, max_usd=left(), stage="review_cost_ops",
        run_dir=run_dir, cap_env="MOVIETECH_REVIEW_MAX_USD")

    objections = (_number([d.model_dump() for d in skeptic.objections], "skeptic", "SK", rnd)
                  + _number(grounder_drafts, "grounder", "GR", rnd)
                  + _number([d.model_dump() for d in cost.objections], "cost_ops", "CO", rnd))
    raiser = {o.objection_id: o.reviewer for o in open_before}  # only the raiser can close
    verdicts += [v for v in skeptic.verdicts if raiser.get(v.objection_id) == "skeptic"]
    verdicts += [v for v in cost.verdicts if raiser.get(v.objection_id) == "cost_ops"]
    closed = {v.objection_id for v in verdicts if v.closed}
    result = ReviewRound(round=rnd, objections=objections, verdicts=verdicts,
                         carried=[o for o in open_before if o.objection_id not in closed],
                         checks={"skeptic": skeptic.checks, "cost_ops": cost.checks},
                         grounding=lookups, not_checkable=deps.not_checkable,
                         monthly_cost_usd=cost.monthly_cost_usd,
                         cost_breakdown=cost.cost_breakdown)
    warnings = ([f"skeptic skipped rubric item: {i}"
                 for i in missing_checks(skeptic.checks, SKEPTIC_RUBRIC)]
                + [f"cost_ops skipped rubric item: {i}"
                   for i in missing_checks(cost.checks, COST_OPS_RUBRIC)])

    (plan_dir / f"review_r{rnd}.json").write_text(result.model_dump_json(indent=2),
                                                  encoding="utf-8")
    (plan_dir / f"review_r{rnd}.md").write_text(to_markdown(result), encoding="utf-8")
    with (run_dir / "debate_log.jsonl").open("a", encoding="utf-8") as f:
        for v in verdicts:
            event = "closed" if v.closed else "still_open"
            f.write(json.dumps({"round": rnd, "event": event, **v.model_dump()}) + "\n")
        for o in objections:
            f.write(json.dumps({"round": rnd, "event": "raised", **o.model_dump()}) + "\n")
    return result, warnings
