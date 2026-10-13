"""Human gate: you pick 1-3 capability cards and give a Vision Brief for each. Plain code, no LLM.

Reads <run_dir>/cards.json and your vision_<name>.md files, checks them, and writes
<run_dir>/gate.json for the Architect. Every problem is reported at once, so you fix
the brief in one go.
"""

import re
from pathlib import Path

from movietech.schemas import CapabilityCards, GateResult, GateSelection, VisionBrief

# Heading in the template -> field name. Headings must match the template.
SECTIONS = {
    "who uses it": "who_uses_it",
    "use case": "use_case",
    "where it runs": "where_it_runs",
    "must-haves": "must_haves",
    "out of scope": "out_of_scope",
    "constraints": "constraints",
    "how i'll know it works": "success_measure",
}
LIST_FIELDS = {"must_haves", "out_of_scope", "constraints"}


class BriefError(ValueError):
    """A Vision Brief is missing something or breaks a rule. The message lists every problem."""


def parse_brief(path: Path) -> VisionBrief:
    """Read a Vision Brief in the template's Markdown format."""
    text = path.read_text(encoding="utf-8-sig")  # -sig: Notepad may add a byte-order mark
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)  # drop the template's hints

    title, current, raw = "", None, {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "---":  # the template's worked example starts here
            break
        if stripped.startswith("# "):
            title = stripped[2:].removeprefix("Vision Brief:").strip()
        elif stripped.startswith("## "):
            heading = stripped[3:].strip().lower().replace("’", "'")
            current = SECTIONS.get(heading)
            if current:
                raw[current] = []
        elif current and stripped:
            raw[current].append(stripped)

    problems = []
    if not title or title == "<capability name>":
        problems.append("the first line must be '# Vision Brief: <a name you choose>'")
    fields = {"title": title}
    for heading, field in SECTIONS.items():
        lines = raw.get(field)
        if lines is None:
            problems.append(f"missing the '## {heading.capitalize()}' heading")
            continue
        if field in LIST_FIELDS:
            items = [ln.lstrip("-*").strip() for ln in lines if ln.startswith(("-", "*"))]
            fields[field] = [i for i in items if i]
        else:
            fields[field] = " ".join(lines)
        if not fields[field] and field != "out_of_scope":
            problems.append(f"'{heading}' is empty")
    if len(fields.get("must_haves", [])) > 5:
        problems.append(f"{len(fields['must_haves'])} must-haves; keep the 5 that matter most")
    if problems:
        raise BriefError(f"{path.name}: " + "; ".join(problems))
    return VisionBrief(**fields)


def run_gate(run_dir: Path, picks: list[tuple[str, Path]]) -> GateResult:
    """Check each (card id, brief file) pair and write gate.json."""
    cards_path = run_dir / "cards.json"
    if not cards_path.exists():
        raise FileNotFoundError(f"{cards_path} not found. Run cards first.")
    cards = {c.card_id: c for c in
             CapabilityCards.model_validate_json(cards_path.read_text(encoding="utf-8")).cards}
    if not 1 <= len(picks) <= 3:
        raise BriefError(f"pick 1 to 3 cards, not {len(picks)}")

    problems, selections = [], []
    for card_id, brief_path in picks:
        card_id = card_id.upper()
        if card_id not in cards:
            problems.append(f"{card_id} is not in cards.json (cards are {min(cards)}..{max(cards)})")
            continue
        if not brief_path.exists():
            problems.append(f"brief file not found: {brief_path}")
            continue
        try:
            brief = parse_brief(brief_path)
        except BriefError as e:
            problems.append(str(e))
            continue
        selections.append(GateSelection(card=cards[card_id], brief=brief,
                                        brief_file=brief_path.name))
    if len({s.card.card_id for s in selections}) < len(selections):
        problems.append("the same card was picked twice")
    if problems:
        raise BriefError("\n".join(problems))

    result = GateResult(selections=selections)
    (run_dir / "gate.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result
