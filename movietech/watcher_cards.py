"""Watcher Pass C, capability cards: Claude merges the deep-look results into a short list of
buildable capabilities, each tied back to the moments in the film that show it.

Reads <run_dir>/deep.jsonl and writes <run_dir>/cards.json (for the next agents) and
<run_dir>/cards.md (for you, at the human gate). One model call; rerun to regenerate.
"""

import json
from pathlib import Path

import anthropic

from movietech import config
from movietech.costs import BudgetExceeded, CostTracker
from movietech.schemas import CapabilityCards, DeepLookResult

SYSTEM = """You are the last stage of a film-watching pipeline. Earlier stages watched a film
segment by segment and listed what its fictional software does. Your job is to turn those notes
into capability cards that a software team could plan from.

Rules:
- Merge capabilities that are the same thing seen in different scenes into one card. Keep
  genuinely different capabilities apart, even if they appear in the same scene.
- Describe each card as a black box: what it does and how people use it, never how it works
  inside. Architects decide the internals later.
- Every card must cite evidence copied from the observations: segment id, time, what happens,
  and the evidence tag as given. Do not invent observations or upgrade their evidence tags.
  strongest_evidence is the strongest tag among the card's evidence
  (observed > necessary > plausible > speculative).
- Judge feasibility honestly against today's technology and say why in one sentence.
- Every raw capability name in the input must appear exactly once: in one card's merged_from,
  or in dropped with a reason (for example: plot device, not software; duplicate wording).
- Order cards from most to least buildable, then by strength of evidence."""


def _notes(results: list[DeepLookResult]) -> str:
    """The deep-look results as compact JSON lines: only what the cards need."""
    keep = {"segment_id", "start", "end", "summary", "observations", "capabilities"}
    return "\n".join(json.dumps(r.model_dump(include=keep)) for r in results)


def _check(cards: CapabilityCards, results: list[DeepLookResult]) -> list[str]:
    """Warnings for anything the model left unaccounted for or cited wrongly."""
    warnings = []
    raw = {c for r in results for c in r.capabilities}
    covered = {n for card in cards.cards for n in card.merged_from}
    covered |= {d.name for d in cards.dropped}
    for name in sorted(raw - covered):
        warnings.append(f"capability not covered by any card or drop: {name}")
    segment_ids = {r.segment_id for r in results}
    for card in cards.cards:
        if not card.evidence:
            warnings.append(f"{card.card_id} has no evidence")
        for ev in card.evidence:
            if ev.segment_id not in segment_ids:
                warnings.append(f"{card.card_id} cites unknown segment {ev.segment_id}")
    return warnings


def to_markdown(cards: CapabilityCards) -> str:
    """A readable version of the cards for the human gate."""
    lines = ["# Capability cards", "",
             ("Pick 1 to 3 cards, then write a Vision Brief for each "
              "(template: vision-brief-template.md)."), ""]
    for c in cards.cards:
        lines += [f"## {c.card_id}: {c.name}", "",
                  f"**What it does:** {c.what_it_does}", "",
                  f"**How people use it:** {c.how_people_use_it}", "",
                  f"**Feasibility:** {c.feasibility.replace('_', ' ')}. {c.feasibility_reason}", "",
                  f"**Strongest evidence:** {c.strongest_evidence}", ""]
        for ev in c.evidence:
            lines.append(f"- {ev.segment_id} at {ev.at:.0f}s ({ev.evidence}): {ev.what_happens}")
        lines.append("")
        if c.real_world_analogues:
            lines += [f"**Similar today (unverified):** {', '.join(c.real_world_analogues)}", ""]
        if c.hardware_needed:
            lines += [f"**Hardware needed:** {', '.join(c.hardware_needed)}", ""]
    if cards.dropped:
        lines += ["## Dropped", ""]
        lines += [f"- {d.name}: {d.reason}" for d in cards.dropped]
        lines.append("")
    return "\n".join(lines)


def make_cards(run_dir: Path, client=None, model: str = config.CARDS_MODEL,
               max_usd: float = config.CARDS_MAX_RUN_USD) -> tuple[CapabilityCards, list[str]]:
    """Run Pass C. Returns the cards and any consistency warnings."""
    deep_path = run_dir / "deep.jsonl"
    if not deep_path.exists():
        raise FileNotFoundError(f"{deep_path} not found. Run deep first.")
    results = [DeepLookResult.model_validate_json(line)
               for line in deep_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = [r for r in results if r.shows_software]
    if not results:
        raise ValueError("No segment in deep.jsonl shows software, so there is nothing to card.")
    notes = _notes(results)

    # One call, so check the worst case before paying: ~3.5 characters per token,
    # plus a full max_tokens answer.
    in_price, out_price = config.PRICES_USD_PER_M[model]
    worst = ((len(SYSTEM) + len(notes)) / 3.5 * in_price
             + config.CARDS_MAX_TOKENS * out_price) / 1_000_000
    if worst > max_usd:
        raise BudgetExceeded(f"Worst case ${worst:.2f} is over the ${max_usd:.2f} cap. "
                             "Raise MOVIETECH_CARDS_MAX_USD to run.")

    client = client or anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    tracker = CostTracker(model, max_usd, stage="cards", ledger=run_dir / "costs.jsonl")
    # Streaming keeps a long answer from hitting the HTTP timeout. "default" fallbacks let the
    # API re-run the request on another model if a safety classifier declines it.
    with client.beta.messages.stream(
        model=model,
        max_tokens=config.CARDS_MAX_TOKENS,
        system=SYSTEM,
        messages=[{"role": "user", "content": f"Deep-look notes, one segment per line:\n{notes}"}],
        output_config={"effort": config.CARDS_EFFORT},
        output_format=CapabilityCards,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        message = stream.get_final_message()
    tracker.add(message.usage, item="all", model=message.model)

    if message.stop_reason == "refusal":
        raise RuntimeError("The model declined to answer. Check deep.jsonl for unusual content.")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("The answer hit the token limit and was cut off. "
                           "Set MOVIETECH_CARDS_EFFORT=medium and rerun.")
    cards = message.parsed_output
    if cards is None:
        raise RuntimeError("The model returned no valid cards. Rerun to retry.")

    warnings = _check(cards, results)
    (run_dir / "cards.json").write_text(cards.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "cards.md").write_text(to_markdown(cards), encoding="utf-8")
    print(f"Estimated spend this run: ${tracker.spent_usd:.4f}")
    return cards, warnings
