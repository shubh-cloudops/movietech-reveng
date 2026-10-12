"""Watcher Pass A, triage: a cheap vision model flags which segments show a software system.

Reads <run_dir>/manifest.json and appends one line per segment to <run_dir>/triage.jsonl.
Segments already in triage.jsonl are skipped, so a stopped run resumes without paying twice.
"""

import time
from pathlib import Path

from google import genai
from google.genai import errors, types

from movietech import config
from movietech.schemas import Manifest, TriageResult, TriageVerdict

PROMPT = """You are triaging a film for software reverse-engineering.
You get keyframes from one segment ({start:.0f}s to {end:.0f}s) and the dialogue spoken in it.

Question: does a software-driven system visibly or audibly act in this segment?
Count: on-screen UIs, displays, HUDs, voice assistants, automated or robotic behaviour,
data visualisations, computers doing something on their own.
Do not count ordinary devices used in an ordinary way (a phone call, driving a car).

Answer "yes", "no" or "unsure", with a one-line reason naming what you saw or heard.

Dialogue:
{dialogue}"""


class BudgetExceeded(RuntimeError):
    """Raised when a run's estimated spend passes MAX_RUN_USD."""


class CostTracker:
    """Adds up estimated spend from each response's token counts."""

    def __init__(self, model: str, max_usd: float):
        self.in_price, self.out_price = config.PRICES_USD_PER_M[model]
        self.max_usd = max_usd
        self.spent_usd = 0.0

    def add(self, usage) -> None:
        prompt = usage.prompt_token_count or 0
        output = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
        self.spent_usd += (prompt * self.in_price + output * self.out_price) / 1_000_000
        if self.spent_usd > self.max_usd:
            raise BudgetExceeded(
                f"Estimated spend ${self.spent_usd:.2f} passed the ${self.max_usd:.2f} cap. "
                "Raise MOVIETECH_MAX_RUN_USD to continue; finished segments are kept."
            )


def _call_with_retry(client, model: str, contents: list, cfg, attempts: int = 4):
    """Retry rate limits (429) and server errors (5xx) with 2s, 4s, 8s backoff."""
    for attempt in range(attempts):
        try:
            return client.models.generate_content(model=model, contents=contents, config=cfg)
        except errors.APIError as e:
            if attempt == attempts - 1 or not (e.code == 429 or e.code >= 500):
                raise
            time.sleep(2 ** (attempt + 1))


def triage(run_dir: Path, client=None, model: str = config.TRIAGE_MODEL,
           max_usd: float = config.MAX_RUN_USD) -> list[TriageResult]:
    """Run Pass A over every segment that has no verdict yet."""
    manifest = Manifest.model_validate_json((run_dir / "manifest.json").read_text(encoding="utf-8"))
    out_path = run_dir / "triage.jsonl"
    done = {}
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            r = TriageResult.model_validate_json(line)
            done[r.segment_id] = r

    client = client or genai.Client()  # reads GEMINI_API_KEY from the environment
    tracker = CostTracker(model, max_usd)
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=TriageVerdict,
        media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
    )

    with out_path.open("a", encoding="utf-8") as out:
        for seg in manifest.segments:
            if seg.segment_id in done:
                continue
            contents = [
                types.Part.from_bytes(data=(run_dir / k).read_bytes(), mime_type="image/jpeg")
                for k in seg.keyframes
            ]
            dialogue = "\n".join(seg.transcript) or "(none)"
            contents.append(PROMPT.format(start=seg.start, end=seg.end, dialogue=dialogue))

            response = _call_with_retry(client, model, contents, cfg)
            verdict = response.parsed
            result = TriageResult(segment_id=seg.segment_id, start=seg.start, end=seg.end,
                                  verdict=verdict.verdict, reason=verdict.reason)
            out.write(result.model_dump_json() + "\n")
            out.flush()
            done[seg.segment_id] = result
            print(f"{seg.segment_id} {seg.start:>7.1f}s  {result.verdict:<6} {result.reason}")
            tracker.add(response.usage_metadata)

    print(f"Estimated spend this run: ${tracker.spent_usd:.4f}")
    return [done[s.segment_id] for s in manifest.segments if s.segment_id in done]
