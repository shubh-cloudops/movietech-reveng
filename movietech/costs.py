"""Cost ledger: every model call is priced and appended to <run_dir>/costs.jsonl.

One ledger for all stages, so `python -m movietech costs <run_dir>` shows where the money went.
"""

import json
import time
from collections import defaultdict
from pathlib import Path

from movietech import config


class BudgetExceeded(RuntimeError):
    """Raised when a stage's estimated spend passes its cap."""


class CostTracker:
    """Prices each response from its token counts, logs it, and stops the run at the cap."""

    def __init__(self, model: str, max_usd: float, stage: str = "", ledger: Path | None = None):
        self.model = model
        self.max_usd = max_usd
        self.stage = stage
        self.ledger = ledger
        self.spent_usd = 0.0

    def add(self, usage, item: str = "", model: str | None = None) -> None:
        """Price one response. `model` overrides the tracker's model, e.g. after a fallback."""
        model = model or self.model
        if hasattr(usage, "prompt_token_count"):  # Gemini
            prompt = usage.prompt_token_count or 0
            answer = usage.candidates_token_count or 0
            thinking = usage.thoughts_token_count or 0
        else:  # Claude: output_tokens already includes thinking, which isn't reported apart
            prompt = ((usage.input_tokens or 0) + (usage.cache_creation_input_tokens or 0)
                      + (usage.cache_read_input_tokens or 0))  # all at full price: errs high
            answer, thinking = usage.output_tokens or 0, 0
        in_price, out_price = config.PRICES_USD_PER_M.get(model, config.UNKNOWN_MODEL_PRICE)
        usd = (prompt * in_price + (answer + thinking) * out_price) / 1_000_000
        self.spent_usd += usd
        if self.ledger:
            row = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": self.stage,
                   "model": model, "item": item, "input_tokens": prompt,
                   "output_tokens": answer, "thinking_tokens": thinking, "usd": round(usd, 6)}
            with self.ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        if self.spent_usd > self.max_usd:
            raise BudgetExceeded(
                f"Estimated spend ${self.spent_usd:.2f} passed the ${self.max_usd:.2f} cap. "
                "Raise the cap to continue; finished segments are kept."
            )


def summarise(run_dir: Path) -> dict[str, dict[str, float]]:
    """Totals per stage from costs.jsonl: calls, input/output/thinking tokens and USD."""
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    path = run_dir / "costs.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            t = totals[row["stage"]]
            t["calls"] += 1
            for key in ("input_tokens", "output_tokens", "thinking_tokens", "usd"):
                t[key] += row[key]
    return {stage: dict(t) for stage, t in totals.items()}
