"""Watcher Pass A, triage: a cheap vision model flags which segments show a software system.

Reads <run_dir>/manifest.json and appends one line per segment to <run_dir>/triage.jsonl.
Segments already in triage.jsonl are skipped, so a stopped run resumes without paying twice.
"""

from pathlib import Path

from google import genai
from google.genai import types

from movietech import config
from movietech.costs import BudgetExceeded, CostTracker
from movietech.gemini import call_with_retry
from movietech.schemas import Manifest, TriageResult, TriageVerdict

__all__ = ["BudgetExceeded", "triage"]

PROMPT = """You are triaging a film for software reverse-engineering.
You get keyframes from one segment ({start:.0f}s to {end:.0f}s) and the dialogue spoken in it.

Question: does a software-driven system visibly or audibly act in this segment?
Count: on-screen UIs, displays, HUDs, voice assistants, automated or robotic behaviour,
data visualisations, computers doing something on their own.
Do not count ordinary devices used in an ordinary way (a phone call, driving a car).

Answer "yes", "no" or "unsure", with a one-line reason naming what you saw or heard.

Dialogue:
{dialogue}"""


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
    tracker = CostTracker(model, max_usd, stage="triage", ledger=run_dir / "costs.jsonl")
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

            response = call_with_retry(client, model, contents, cfg)
            verdict = response.parsed
            result = TriageResult(segment_id=seg.segment_id, start=seg.start, end=seg.end,
                                  verdict=verdict.verdict, reason=verdict.reason)
            out.write(result.model_dump_json() + "\n")
            out.flush()
            done[seg.segment_id] = result
            print(f"{seg.segment_id} {seg.start:>7.1f}s  {result.verdict:<6} {result.reason}")
            tracker.add(response.usage_metadata, item=seg.segment_id)

    print(f"Estimated spend this run: ${tracker.spent_usd:.4f}")
    return [done[s.segment_id] for s in manifest.segments if s.segment_id in done]
