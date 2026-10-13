"""Shared helper for one structured Claude call: budget pre-check, streaming, fallbacks, cost log."""

from pathlib import Path

import anthropic

from movietech import config
from movietech.costs import BudgetExceeded, CostTracker


def structured_call(*, client, model: str, system: str, user: str, output_format,
                    effort: str, max_tokens: int, max_usd: float, stage: str, run_dir: Path,
                    cap_env: str):
    """Ask Claude for one answer shaped like `output_format` (a Pydantic model) and return it.

    The worst case (prompt at ~3.5 characters per token plus a full max_tokens answer) is
    checked against max_usd before any money is spent.
    """
    in_price, out_price = config.PRICES_USD_PER_M[model]
    worst = ((len(system) + len(user)) / 3.5 * in_price + max_tokens * out_price) / 1_000_000
    if worst > max_usd:
        raise BudgetExceeded(f"Worst case ${worst:.2f} is over the ${max_usd:.2f} cap. "
                             f"Raise {cap_env} to run.")

    client = client or anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    tracker = CostTracker(model, max_usd, stage=stage, ledger=run_dir / "costs.jsonl")
    # Streaming keeps a long answer from hitting the HTTP timeout. "default" fallbacks let the
    # API re-run the request on another model if a safety classifier declines it.
    with client.beta.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort},
        output_format=output_format,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        message = stream.get_final_message()
    tracker.add(message.usage, item="all", model=message.model)

    if message.stop_reason == "refusal":
        raise RuntimeError("The model declined to answer. Check the input for unusual content.")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("The answer hit the token limit and was cut off. "
                           "Lower the effort setting for this stage and rerun.")
    if message.parsed_output is None:
        raise RuntimeError("The model returned no valid answer. Rerun to retry.")
    print(f"Estimated spend this run: ${tracker.spent_usd:.4f}")
    return message.parsed_output
