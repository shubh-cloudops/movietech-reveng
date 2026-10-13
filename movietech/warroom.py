"""War Room loop: review → Chairman rules → authors revise → review again, until the hard gates
pass or the round cap is reached. Then you decide at the final approval gate.

Each step writes its files before the next starts, so a stopped run picks up where it left off.
Every version of the plan is kept in plan/history/v<N>/ so you can show how it improved.
"""

import json
import shutil
from pathlib import Path

from movietech import architect, chairman, config, lead_dev, review, tester
from movietech.claude import structured_call
from movietech.costs import summarise
from movietech.schemas import (
    AcceptancePlan,
    Architecture,
    BuildPlan,
    LedgerEntry,
    ReviewRound,
)

REVISE = """

You are now revising your earlier work after a review. Fix every objection listed below.
Return the whole updated document. Change only what the objections require; keep every other
part, and every existing id, exactly as it was. If an objection asks for something the Vision
Brief rules out, follow the brief and leave that part unchanged."""

# owner → (model, author's system prompt, schema, file name, markdown writer)
AUTHORS = {
    "architect": (config.ARCHITECT_MODEL, architect.SYSTEM, Architecture, "architecture",
                  architect.to_markdown),
    "lead_dev": (config.LEAD_DEV_MODEL, lead_dev.SYSTEM, BuildPlan, "tasks",
                 lead_dev.to_markdown),
    "test_eng": (config.TEST_ENG_MODEL, tester.SYSTEM, AcceptancePlan, "tests",
                 tester.to_markdown),
}
FILES = ("architecture.json", "tasks.json", "tests.json")


def _spent(run_dir: Path) -> float:
    return sum(t["usd"] for t in summarise(run_dir).values())


def _latest_round(plan_dir: Path) -> ReviewRound | None:
    rounds = sorted(plan_dir.glob("review_r*.json"), key=lambda p: int(p.stem[8:]))
    if not rounds:
        return None
    return ReviewRound.model_validate_json(rounds[-1].read_text(encoding="utf-8"))


def revise(run_dir: Path, ledger: dict[str, LedgerEntry], rnd: int, client=None,
           max_usd: float = config.WARROOM_MAX_RUN_USD) -> list[str]:
    """Send each author the open objections it owns, in plan order (architecture first, since
    tasks and tests depend on it), plus any gap the code checks find downstream."""
    plan_dir = run_dir / "plan"
    history = plan_dir / "history" / f"v{rnd}"
    if not history.exists():  # keep the version the reviewers just saw
        history.mkdir(parents=True)
        for name in FILES:
            shutil.copy(plan_dir / name, history / name)

    revised = []
    start = _spent(run_dir)
    for owner, (model, system, schema, name, to_md) in AUTHORS.items():
        gate, arch = lead_dev.load_inputs(run_dir)
        items = [e.objection.model_dump(include={"objection_id", "section", "severity",
                                                 "issue", "evidence", "proposed_fix"})
                 for e in ledger.values() if e.status == "open" and e.owner == owner]
        # Downstream sync: if the architecture changed, tasks must still cover it, and so on.
        if owner == "lead_dev" and revised:
            tasks = BuildPlan.model_validate_json((plan_dir / "tasks.json").read_text("utf-8"))
            items += [{"code_check": p} for p in lead_dev.check(tasks, arch, gate)]
        if owner == "test_eng" and revised:
            tasks = BuildPlan.model_validate_json((plan_dir / "tasks.json").read_text("utf-8"))
            tests = AcceptancePlan.model_validate_json(
                (plan_dir / "tests.json").read_text("utf-8"))
            items += [{"code_check": p} for p in tester.check(tests, tasks, gate)]
        if not items:
            continue
        current = (plan_dir / f"{name}.json").read_text(encoding="utf-8")
        context = lead_dev.briefs_text(gate)
        if owner != "architect":
            context += f"\n\nARCHITECTURE {arch.model_dump_json()}"
        if owner == "test_eng":
            context += f"\n\nBUILD PLAN {(plan_dir / 'tasks.json').read_text('utf-8')}"
        updated = structured_call(
            client=client, model=model, system=system + REVISE,
            user=(f"{context}\n\nYOUR CURRENT {name.upper()} {current}\n\n"
                  f"OBJECTIONS TO FIX\n{json.dumps(items)}"),
            output_format=schema, effort=config.REVISE_EFFORT, max_tokens=32000,
            max_usd=max_usd - (_spent(run_dir) - start), stage=f"revise_{owner}",
            run_dir=run_dir, cap_env="MOVIETECH_WARROOM_MAX_USD")
        (plan_dir / f"{name}.json").write_text(updated.model_dump_json(indent=2), "utf-8")
        (plan_dir / f"{name}.md").write_text(to_md(updated), encoding="utf-8")
        revised.append(owner)
        with (run_dir / "debate_log.jsonl").open("a", encoding="utf-8") as f:
            for i in items:
                if "objection_id" in i:
                    f.write(json.dumps({"round": rnd, "event": "revised", "owner": owner,
                                        "objection_id": i["objection_id"]}) + "\n")
    return revised


def summary(run_dir: Path, ledger: dict[str, LedgerEntry], r: ReviewRound,
            failed: list[str], stopped: str) -> str:
    """plan/warroom.md: where the debate ended and what is left for you."""
    lines = ["# War Room", "", f"**Result:** {stopped}", f"**Rounds:** {r.round}", ""]
    if failed:
        lines += ["## Gates still failing", ""] + [f"- {g}" for g in failed] + [""]
    asks = [e for e in ledger.values() if e.status == "ask_user"]
    if asks:
        lines += ["## Questions for you", ""]
        lines += [f"- {e.objection.objection_id}: {e.question_for_user}" for e in asks]
        lines.append("")
    groups = [("open", "Still open"), ("closed", "Fixed and closed by the reviewer who raised it"),
              ("rejected", "Rejected by the Chairman (decision record)")]
    for status, title in groups:
        rows = [e for e in ledger.values() if e.status == status]
        if rows:
            lines += [f"## {title}", ""]
            for e in rows:
                why = e.closed_reason if status == "closed" else e.ruling_reason
                lines.append(f"- {e.objection.objection_id} ({e.objection.severity}, "
                             f"{e.objection.reviewer}): {e.objection.issue} *{why}*")
            lines.append("")
    return "\n".join(lines)


def warroom(run_dir: Path, claude=None, gemini_client=None, fetch=None,
            max_rounds: int = config.WARROOM_MAX_ROUNDS,
            max_usd: float = config.WARROOM_MAX_RUN_USD) -> tuple[ReviewRound, list[str], str]:
    """Run the debate. Returns the last review round, the gates still failing, and why it
    stopped."""
    plan_dir = run_dir / "plan"
    start = _spent(run_dir)

    def left() -> float:
        return max_usd - (_spent(run_dir) - start)

    ledger = chairman.load_ledger(run_dir)
    r = _latest_round(plan_dir)
    while True:
        if r is None or (plan_dir / f"revised_r{r.round}").exists():
            if r is not None and r.round >= max_rounds:
                break
            r, _ = review.review(run_dir, claude=claude, gemini_client=gemini_client,
                                 fetch=fetch, max_usd=min(left(), config.REVIEW_MAX_RUN_USD),
                                 open_before=chairman.open_objections(ledger))
        chairman.record_review(ledger, r)
        chairman.save_ledger(run_dir, ledger)
        failed = review.gates(r)
        if not failed or r.round >= max_rounds:
            break
        gate, arch, tasks, tests = review._load(run_dir)
        chairman.triage(run_dir, review.plan_text(gate, arch, tasks, tests), ledger, r.round,
                        client=claude, max_usd=left())
        chairman.save_ledger(run_dir, ledger)
        revised = revise(run_dir, ledger, r.round, client=claude, max_usd=left())
        (plan_dir / f"revised_r{r.round}").touch()  # marks the round done, for resuming
        if not revised:  # nothing an author can fix: another round would cost money for nothing
            break

    failed = review.gates(r)
    if not failed:
        stopped = "All gates pass. Ready for your approval."
    elif r.round >= max_rounds:
        stopped = (f"Stopped at the {max_rounds}-round cap with gates failing. You decide: "
                   "approve with the open issues listed, or edit your brief and rerun.")
    else:
        stopped = ("Stopped: the failing gates need your decision, not another revision "
                   "(see the questions below).")
    (plan_dir / "warroom.md").write_text(summary(run_dir, ledger, r, failed, stopped), "utf-8")
    return r, failed, stopped


def approve(run_dir: Path, notes: str = "") -> Path:
    """The final gate: you sign off. Writes plan/PLAN.md (the whole plan in one file) and
    plan/approval.json, recording any objections still open, so the decision is on record."""
    plan_dir = run_dir / "plan"
    if not (plan_dir / "warroom.md").exists():
        raise FileNotFoundError(f"{plan_dir / 'warroom.md'} not found. Run warroom first.")
    ledger = chairman.load_ledger(run_dir)
    r = _latest_round(plan_dir)
    still_open = [e.objection.objection_id for e in ledger.values()
                  if e.status in ("open", "ask_user")]
    record = {"approved": True, "round": r.round if r else 0, "notes": notes,
              "gates_failing": review.gates(r) if r else [], "open_objections": still_open}
    (plan_dir / "approval.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    parts = [(plan_dir / "warroom.md").read_text(encoding="utf-8")]
    for _, _, schema, name, to_md in AUTHORS.values():  # rendered from the final JSON
        parts.append(to_md(schema.model_validate_json(
            (plan_dir / f"{name}.json").read_text(encoding="utf-8"))))
    body = "\n\n---\n\n".join(parts)
    header = "# Project plan (approved)\n\n"
    if notes:
        header += f"**Approval notes:** {notes}\n\n"
    (plan_dir / "PLAN.md").write_text(header + body, encoding="utf-8")
    return plan_dir / "PLAN.md"
