"""Clips-and-notes input: for a film you don't own a copy of (Iron Man), instead of ingest,
triage and deep.

Two sources, both optional, both written to <run_dir>/deep.jsonl in the same format as Pass B,
so `cards` and every later stage run unchanged:
- Official YouTube clips. Gemini watches each public clip straight from its URL, so the video
  is never downloaded. Triage is skipped: you already chose the clips. Ids Y01, Y02, ...
- Your own scene notes (ironman_notes.md). Parsed by plain code, no model and no cost.
  Ids N01, N02, ... Each note becomes one "observed" observation, because you saw it yourself.

Finished clips and notes are skipped on rerun, so a stopped run resumes without paying twice.
"""

import re
from pathlib import Path

from google import genai
from google.genai import errors, types

from movietech import config
from movietech.costs import CostTracker
from movietech.gemini import call_with_retry
from movietech.schemas import DeepLook, DeepLookResult, Observation
from movietech.watcher_deep import SOFTWARE_FOCUS, _config, _load_jsonl

CLIP_PROMPT = """You are reverse-engineering the fictional software shown in a film.
This is an official clip from the film, titled "{title}".

Watch the whole clip, including the audio.
{focus}

For each observation, give the time in seconds from the start of THIS clip and tag the evidence:
- observed: you can see or hear it in the clip
- necessary: it must exist for what you see to work (say what implies it)
- plausible: likely, but not shown
- speculative: a guess
Prefer fewer, well-evidenced observations over many vague ones.
If the clip shows no software at all (only machines, props or credits), set shows_software to
false and leave observations and capabilities empty."""

_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})")


def seconds(hms: str) -> float:
    """'01:02:10' -> 3730.0"""
    m = _TIME.fullmatch(hms.strip())
    if not m:
        raise ValueError(f"not a HH:MM:SS time: {hms!r}")
    h, mi, s = (int(x) for x in m.groups())
    return float(h * 3600 + mi * 60 + s)


def read_clips(path: Path) -> list[tuple[str, str, float]]:
    """One clip per line: `URL | title | film start (optional HH:MM:SS)`. Lines starting with #
    are comments. Returns (url, title, film start in seconds, 0 if not given)."""
    clips = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        url = parts[0]
        if not re.match(r"https://(www\.)?(youtube\.com/watch\?v=|youtu\.be/)", url):
            raise ValueError(f"{path.name} line {n}: not a YouTube link: {url}")
        title = parts[1] if len(parts) > 1 and parts[1] else f"clip {len(clips) + 1}"
        start = seconds(parts[2]) if len(parts) > 2 and parts[2] else 0.0
        clips.append((url, title, start))
    return clips


_FIELD = re.compile(r"^-\s*\*\*(.+?):\*\*\s*(.*)$")
_INPUTS = {"voice": "voice", "gesture": "gesture", "touch": "touch", "gaze": "gaze",
           "keyboard": "keyboard", "sensor": "automatic", "none": "automatic"}


def read_notes(path: Path) -> list[DeepLookResult]:
    """Parse the scene-notes template. Only `## Scene` blocks count; the example and any block
    still holding the 00:00:00 placeholder time are skipped."""
    results = []
    blocks = re.split(r"^##\s+", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    for block in blocks:
        if not block.lower().startswith("scene"):
            continue
        fields = {}
        for line in block.splitlines()[1:]:
            m = _FIELD.match(line.strip())
            if m:
                fields[m.group(1).strip().lower()] = m.group(2).strip()
        times = _TIME.findall(fields.get("time", ""))
        if len(times) < 2 or not fields.get("what happens"):
            continue
        start, end = (seconds(":".join(t)) for t in times[:2])
        if start == end == 0:
            continue
        system = fields.get("system", "the system")
        what = fields["what happens"]
        for key, label in (("on-screen text", "On screen"), ("dialogue with the system", "Said"),
                           ("anything notable", "Notable")):
            value = fields.get(key, "").strip()
            if value and value.strip('"') not in ("", "...", "none"):
                what += f" {label}: {value}"
        given = fields.get("input the system got", "").lower()
        user_input = next((v for k, v in _INPUTS.items() if k in given), "other")
        results.append(DeepLookResult(
            segment_id=f"N{len(results) + 1:02d}", start=start, end=end, model="your notes",
            shows_software=True, summary=f"{system}: {fields['what happens']}",
            observations=[Observation(at=start, what_happens=what,
                                      interface=fields.get("output the system gave", "") or "none",
                                      user_input=user_input, evidence="observed")],
            capabilities=[]))
    return results


def watch_clips(run_dir: Path, clips_file: Path | None = None, notes_file: Path | None = None,
                client=None, model: str = config.DEEP_MODEL,
                max_usd: float = config.DEEP_MAX_RUN_USD) -> tuple[list[DeepLookResult], list[str]]:
    """Add your notes and a deep look at each clip to deep.jsonl. Returns all results and any
    warnings (e.g. a clip Gemini could not open)."""
    if clips_file is None and notes_file is None:
        raise ValueError("Give --clips, --notes, or both.")
    run_dir.mkdir(parents=True, exist_ok=True)
    out_path = run_dir / "deep.jsonl"
    done = {r.segment_id: r for r in _load_jsonl(out_path, DeepLookResult)}
    warnings = []

    with out_path.open("a", encoding="utf-8") as out:
        if notes_file is not None:
            for r in read_notes(notes_file):
                if r.segment_id not in done:
                    out.write(r.model_dump_json() + "\n")
                    done[r.segment_id] = r
            if not any(k.startswith("N") for k in done):
                warnings.append(f"no filled-in scenes found in {notes_file.name}")

        clips = read_clips(clips_file) if clips_file is not None else []
        todo = [(f"Y{i:02d}", c) for i, c in enumerate(clips, 1) if f"Y{i:02d}" not in done]
        if todo:
            client = client or genai.Client()  # reads GEMINI_API_KEY from the environment
            tracker = CostTracker(model, max_usd, stage="clips", ledger=run_dir / "costs.jsonl")
            cfg = _config(model)
            for seg_id, (url, title, film_start) in todo:
                video = types.Part(file_data=types.FileData(file_uri=url),
                                   video_metadata=types.VideoMetadata(fps=config.DEEP_FPS))
                try:
                    prompt = CLIP_PROMPT.format(title=title, focus=SOFTWARE_FOCUS)
                    response = call_with_retry(client, model, [video, prompt], cfg)
                except errors.ClientError as e:  # private, removed or region-blocked video
                    warnings.append(f"{seg_id} skipped, Gemini could not open {url}: {e.message}")
                    continue
                look: DeepLook | None = response.parsed
                tracker.add(response.usage_metadata, item=seg_id)
                if look is None:
                    warnings.append(f"{seg_id} returned no valid answer; rerun to retry")
                    continue
                for obs in look.observations:  # clip time -> film time, when you gave it
                    obs.at = round(film_start + obs.at, 1)
                end = max((o.at for o in look.observations), default=film_start)
                result = DeepLookResult(segment_id=seg_id, start=film_start, end=end,
                                        model=model, **look.model_dump())
                result.summary = f"[{title}] {result.summary}"
                out.write(result.model_dump_json() + "\n")
                out.flush()
                done[seg_id] = result
                label = "software" if result.shows_software else "no software"
                print(f"{seg_id} {label:<11} {len(result.observations)} obs  "
                      f"{'; '.join(result.capabilities)}")
            print(f"Estimated spend this run: ${tracker.spent_usd:.4f}")

    return list(done.values()), warnings
