"""Architect: turns the cards and Vision Briefs you accepted at the gate into a system design.

Reads <run_dir>/gate.json and writes <run_dir>/plan/architecture.json and architecture.md.
One Claude call. Code then checks the design against your briefs, because an author should not
grade its own work.
"""

from pathlib import Path

from movietech import config
from movietech.claude import structured_call
from movietech.schemas import Architecture, GateResult

SYSTEM = """You are the Architect in a pipeline that turns software seen in films into real,
buildable projects. You receive capability cards (what the film shows, with timestamped
evidence) and, for each one, the user's Vision Brief (who it is for and how they want it used).

Design one system that delivers every picked card the way its brief asks.

Rules:
- The Vision Brief wins on purpose, users, platform and constraints. The film only shows what
  the capability does. Where they disagree, do not choose silently: add a conflict with a
  question for the user, and design for the brief in the meantime.
- Every must-have must be served by at least one component. Copy must-haves word for word
  into serves_must_haves.
- Build nothing listed under "Out of scope".
- Prefer well-known, actively maintained, industry-standard tools that a junior cloud
  engineer can explain in an interview. If something simpler gives 90% of the result, choose
  it and name the advanced option in that ADR.
- Default cloud: AWS, free tier first, unless a brief's constraints say otherwise. Secrets go
  in a secret store or environment variables, never in code.
- Write one ADR for each significant choice (language, hosting, each external service), each
  with at least two real options.
- Data flows reference component ids or 'user' only.
- List assumptions you had to make and the main risks with mitigations."""


def _input(gate: GateResult) -> str:
    """The picked cards and briefs as text for the model."""
    parts = []
    for s in gate.selections:
        parts.append(f"CARD {s.card.model_dump_json(exclude={'merged_from'})}\n"
                      f"VISION BRIEF {s.brief.model_dump_json()}")
    return "\n\n".join(parts)


def check(arch: Architecture, gate: GateResult) -> list[str]:
    """Problems a reviewer should not have to find by hand."""
    problems = []
    ids = {c.component_id for c in arch.components}
    covered = {m for c in arch.components for m in c.serves_must_haves}
    for s in gate.selections:
        for m in s.brief.must_haves:
            if m not in covered:
                problems.append(f"must-have not served by any component: {m}")
        if not any(s.card.card_id in c.serves_cards for c in arch.components):
            problems.append(f"card {s.card.card_id} is not served by any component")
    for f in arch.data_flows:
        for end in (f.source, f.target):
            if end != "user" and end not in ids:
                problems.append(f"data flow uses unknown component {end}")
    for adr in arch.adrs:
        if len(adr.options) < 2:
            problems.append(f"{adr.adr_id} weighs fewer than two options")
    if not arch.adrs:
        problems.append("no ADRs: every significant choice needs one")
    return problems


def to_markdown(arch: Architecture) -> str:
    lines = ["# Architecture", "", arch.summary, ""]
    if arch.conflicts:
        lines += ["## Questions for you (film vs. your brief)", ""]
        lines += [f"- **Film:** {c.film_shows} **Brief:** {c.brief_says} "
                  f"**Question:** {c.question_for_user}" for c in arch.conflicts]
        lines.append("")
    lines += ["## Components", "", "| Id | Name | Does | Technology | Cloud | Serves |",
              "|---|---|---|---|---|---|"]
    for c in arch.components:
        lines.append(f"| {c.component_id} | {c.name} | {c.responsibility} | {c.technology} | "
                     f"{c.cloud_service} | {', '.join(c.serves_cards)} |")
    lines += ["", "## Data flow", ""]
    lines += [f"- {f.source} → {f.target}: {f.data} ({f.protocol})" for f in arch.data_flows]
    lines += ["", "## Decisions (ADRs)", ""]
    for a in arch.adrs:
        lines += [f"### {a.adr_id}: {a.title}", "", f"**Context:** {a.context}", ""]
        for o in a.options:
            lines.append(f"- **{o.option}**. Pros: {'; '.join(o.pros)}. "
                         f"Cons: {'; '.join(o.cons)}.")
        lines += ["", f"**Decision:** {a.decision}", "",
                  f"**Consequences:** {a.consequences}", ""]
    lines += ["## Assumptions", ""] + [f"- {x}" for x in arch.assumptions]
    lines += ["", "## Risks", ""]
    lines += [f"- ({r.likelihood}) {r.risk} Mitigation: {r.mitigation}" for r in arch.risks]
    return "\n".join(lines) + "\n"


def architect(run_dir: Path, client=None, model: str = config.ARCHITECT_MODEL,
              max_usd: float = config.ARCHITECT_MAX_RUN_USD) -> tuple[Architecture, list[str]]:
    """Run the Architect. Returns the design and any problems the code check found."""
    gate_path = run_dir / "gate.json"
    if not gate_path.exists():
        raise FileNotFoundError(f"{gate_path} not found. Run gate first.")
    gate = GateResult.model_validate_json(gate_path.read_text(encoding="utf-8"))

    arch = structured_call(
        client=client, model=model, system=SYSTEM, user=_input(gate),
        output_format=Architecture, effort=config.ARCHITECT_EFFORT,
        max_tokens=config.ARCHITECT_MAX_TOKENS, max_usd=max_usd, stage="architect",
        run_dir=run_dir, cap_env="MOVIETECH_ARCHITECT_MAX_USD",
    )
    problems = check(arch, gate)
    plan_dir = run_dir / "plan"
    plan_dir.mkdir(exist_ok=True)
    (plan_dir / "architecture.json").write_text(arch.model_dump_json(indent=2), encoding="utf-8")
    (plan_dir / "architecture.md").write_text(to_markdown(arch), encoding="utf-8")
    return arch, problems
