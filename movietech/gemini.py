"""Shared Gemini call helper."""

import time
from pathlib import Path

from google.genai import errors, types

from movietech.costs import CostTracker


def call_with_retry(client, model: str, contents: list, cfg, attempts: int = 4):
    """Retry rate limits (429) and server errors (5xx) with 2s, 4s, 8s backoff."""
    for attempt in range(attempts):
        try:
            return client.models.generate_content(model=model, contents=contents, config=cfg)
        except errors.APIError as e:
            if attempt == attempts - 1 or not (e.code == 429 or e.code >= 500):
                raise
            time.sleep(2 ** (attempt + 1))


def structured_call(*, client, model: str, system: str, user: str, schema, thinking: str,
                    max_tokens: int, max_usd: float, stage: str, run_dir: Path, cap_env: str):
    """One text-only Gemini call that returns `schema` (a Pydantic model), with the same budget
    pre-check and cost ledger as the Claude helper."""
    from movietech.claude import check_worst_case

    check_worst_case(model, system + user, max_tokens, max_usd, cap_env)
    if client is None:
        from google import genai

        client = genai.Client()  # reads GEMINI_API_KEY from the environment
    cfg = types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens,
                                      response_mime_type="application/json",
                                      response_schema=schema)
    if model.startswith("gemini-3"):  # older models take a token budget, not a level
        cfg.thinking_config = types.ThinkingConfig(thinking_level=thinking.upper())
    tracker = CostTracker(model, max_usd, stage=stage, ledger=run_dir / "costs.jsonl")
    response = call_with_retry(client, model, [user], cfg)
    tracker.add(response.usage_metadata, item="all")
    if response.parsed is None:
        raise RuntimeError(f"{model} returned no valid answer. Rerun to retry.")
    print(f"Estimated spend this call ({stage}): ${tracker.spent_usd:.4f}")
    return response.parsed
