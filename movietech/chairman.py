"""Chairman: rules on each objection (accept, reject or ask the user) and routes accepted ones
to the author who owns that part of the plan. It never writes plan content, because a judge
that also authors would approve its own work.

Also keeps the objection ledger, plan/objections.json: every objection's status over the
whole War Room (new, open, closed, rejected, ask_user).
"""

import json
from pathlib import Path

from movietech import config
from movietech.claude import structured_call
from movietech.schemas import LedgerEntry, Objection, ReviewRound, Ruling, Triage

SYSTEM = """You are the Chairman of a War Room that reviews a software plan built from a film.
Reviewers raised objections. Rule on each one; you never write or edit the plan yourself.

- accept: the problem is real and fixing it serves the user's Vision Brief. Name the owner who
  fixes it: architect (components, ADRs, data flows, technology choices), lead_dev (tasks,
  milestones, teardown, paid resources) or test_eng (acceptance tests, CI).
- reject: the objection is wrong, asks for something out of scope, or contradicts the brief.
  Give the reason in one line; it is recorded as a decision.
- ask_user: only the user can decide, e.g. vague or conflicting wording in the brief, or a
  trade-off between two must-haves. Write one short question they can answer. The authors keep
  designing for the brief as written meanwhile.
Return exactly one ruling per objection id."""

LEDGER = "objections.json"


def load_ledger(run_dir: Path) -> dict[str, LedgerEntry]:
    path = run_dir / "plan" / LEDGER
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: LedgerEntry.model_validate(v) for k, v in raw.items()}


def save_ledger(run_dir: Path, ledger: dict[str, LedgerEntry]) -> None:
    (run_dir / "plan" / LEDGER).write_text(
        json.dumps({k: v.model_dump() for k, v in ledger.items()}, indent=2), encoding="utf-8")


def record_review(ledger: dict[str, LedgerEntry], r: ReviewRound) -> None:
    """Add a round's new objections and apply the raisers' verdicts. Safe to call twice."""
    for o in r.objections:
        ledger.setdefault(o.objection_id, LedgerEntry(objection=o, round_raised=r.round))
    for v in r.verdicts:
        if v.closed and v.objection_id in ledger:
            ledger[v.objection_id].status = "closed"
            ledger[v.objection_id].closed_reason = v.reason


def open_objections(ledger: dict[str, LedgerEntry]) -> list[Objection]:
    """Accepted objections the raiser has not closed yet."""
    return [e.objection for e in ledger.values() if e.status == "open"]


def default_owner(section: str) -> str:
    """Fallback routing by the id an objection cites: A1/ADR-001 → architect, T01 → lead_dev,
    AT01 → test_eng."""
    s = section.strip().upper()
    if s.startswith("AT") or s == "TESTS":
        return "test_eng"
    if s.startswith("T") or s == "TASKS":
        return "lead_dev"
    return "architect"


def apply_rulings(ledger: dict[str, LedgerEntry], rulings: list[Ruling]) -> list[str]:
    """Record the Chairman's rulings on new objections. Returns warnings for gaps the code fixed:
    a new objection with no ruling is accepted (an author looks at it rather than nobody)."""
    warnings = []
    by_id = {r.objection_id: r for r in rulings}
    for oid, e in ledger.items():
        if e.status != "new":
            continue
        r = by_id.get(oid)
        if r is None:
            warnings.append(f"no ruling for {oid}; accepted and routed by its section")
            r = Ruling(objection_id=oid, decision="accept", owner="none", reason="no ruling")
        if r.decision == "accept":
            owner = r.owner if r.owner != "none" else default_owner(e.objection.section)
            e.status, e.owner = "open", owner
        elif r.decision == "reject":
            e.status = "rejected"
        else:
            e.status, e.question_for_user = "ask_user", r.question_for_user
        e.ruling_reason = r.reason
    return warnings


def triage(run_dir: Path, plan: str, ledger: dict[str, LedgerEntry], rnd: int, client=None,
           max_usd: float = config.WARROOM_MAX_RUN_USD) -> list[str]:
    """Ask the Chairman to rule on this round's new objections and record the rulings."""
    new = [e.objection.model_dump() for e in ledger.values() if e.status == "new"]
    if not new:
        return []
    result = structured_call(
        client=client, model=config.CHAIRMAN_MODEL, system=SYSTEM,
        user=f"{plan}\n\nOBJECTIONS TO RULE ON\n{json.dumps(new)}", output_format=Triage,
        effort=config.CHAIRMAN_EFFORT, max_tokens=config.CHAIRMAN_MAX_TOKENS, max_usd=max_usd,
        stage="chairman", run_dir=run_dir, cap_env="MOVIETECH_WARROOM_MAX_USD")
    warnings = apply_rulings(ledger, result.rulings)
    with (run_dir / "debate_log.jsonl").open("a", encoding="utf-8") as f:
        for r in result.rulings:
            f.write(json.dumps({"round": rnd, "event": r.decision, **r.model_dump()}) + "\n")
    return warnings
