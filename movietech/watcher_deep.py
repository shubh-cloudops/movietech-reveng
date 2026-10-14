"""Watcher Pass B, deep look: a Pro vision model watches the real video of each flagged segment.

Only segments that triage marked "yes" or "unsure" are sent. Each one is cut into a small clip
(<run_dir>/clips/S004.mp4, reused on rerun) and sent with its dialogue and the triage reason.
Results are appended to <run_dir>/deep.jsonl; finished segments are skipped on rerun.
"""

import subprocess
from pathlib import Path

from google import genai
from google.genai import types

from movietech import config
from movietech.costs import CostTracker
from movietech.gemini import call_with_retry
from movietech.schemas import DeepLook, DeepLookResult, Manifest, Segment, TriageResult

# This project builds software only. Shared by Pass B and the clips input.
SOFTWARE_FOCUS = """Look only at software: the user interface, its visual design and how the
software works. For each observation, describe:
- the interface: layout, panels, colours, typography, icons, animation and transitions
- the interaction: what the person does (voice, gesture, touch, gaze) and how the UI responds
- the behaviour: what the software works out, decides or does on its own, and in what order
If a UI is shown as a hologram or on a helmet visor, describe the UI itself as if it were on a
screen. Ignore hardware: suits, robots, weapons, engines, reactors, vehicles and other physical
devices, and anything the plot, acting or props drive rather than software. Name capabilities
as software a small team could build for a normal computer or phone."""

PROMPT = """You are reverse-engineering the fictional software shown in a film.
This clip is {start:.0f}s to {end:.0f}s of the film. A first pass flagged it because: {reason}

Watch the whole clip, including the audio.
{focus}

For each observation, give the time in seconds from the start of THIS clip and tag the evidence:
- observed: you can see or hear it in the clip
- necessary: it must exist for what you see to work (say what implies it)
- plausible: likely, but not shown
- speculative: a guess
Prefer fewer, well-evidenced observations over many vague ones.
If the clip shows no software at all (only machines, props or credits), set shows_software to
false and leave observations and capabilities empty.

Dialogue in this clip:
{dialogue}"""


def cut_clip(video: Path, seg: Segment, clips_dir: Path) -> Path:
    """Cut one segment to a small MP4 with ffmpeg. Reuses an existing clip."""
    clip = clips_dir / f"{seg.segment_id}.mp4"
    if clip.exists() and clip.stat().st_size > 0:
        return clip
    clips_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{seg.start:.2f}", "-i", str(video),
         "-t", f"{seg.end - seg.start:.2f}", "-vf", f"scale=-2:{config.CLIP_HEIGHT}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
         "-maxrate", config.CLIP_MAX_BITRATE, "-bufsize", "2M",
         "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(clip)],
        check=True,
    )
    return clip


def _load_jsonl(path: Path, model):
    if not path.exists():
        return []
    return [model.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _config(model: str) -> types.GenerateContentConfig:
    cfg = types.GenerateContentConfig(response_mime_type="application/json",
                                      response_schema=DeepLook)
    if model.startswith("gemini-3"):  # older models take a token budget, not a level
        cfg.thinking_config = types.ThinkingConfig(thinking_level=config.DEEP_THINKING.upper())
    return cfg


def deep_look(run_dir: Path, video: Path, client=None, model: str = config.DEEP_MODEL,
              max_usd: float = config.DEEP_MAX_RUN_USD) -> list[DeepLookResult]:
    """Run Pass B over every flagged segment that has no deep look yet."""
    manifest = Manifest.model_validate_json((run_dir / "manifest.json").read_text(encoding="utf-8"))
    triage_path = run_dir / "triage.jsonl"
    if not triage_path.exists():
        raise FileNotFoundError(f"{triage_path} not found. Run triage first.")
    if not video.exists():
        raise FileNotFoundError(f"Video not found: {video}")

    reasons = {t.segment_id: t.reason
               for t in _load_jsonl(triage_path, TriageResult) if t.verdict != "no"}
    out_path = run_dir / "deep.jsonl"
    done = {r.segment_id: r for r in _load_jsonl(out_path, DeepLookResult)}

    client = client or genai.Client()  # reads GEMINI_API_KEY from the environment
    tracker = CostTracker(model, max_usd, stage="deep", ledger=run_dir / "costs.jsonl")
    cfg = _config(model)

    with out_path.open("a", encoding="utf-8") as out:
        for seg in manifest.segments:
            if seg.segment_id not in reasons or seg.segment_id in done:
                continue
            clip = cut_clip(video, seg, run_dir / "clips")
            video_part = types.Part(
                inline_data=types.Blob(data=clip.read_bytes(), mime_type="video/mp4"),
                video_metadata=types.VideoMetadata(fps=config.DEEP_FPS),
            )
            prompt = PROMPT.format(start=seg.start, end=seg.end, reason=reasons[seg.segment_id],
                                   focus=SOFTWARE_FOCUS,
                                   dialogue="\n".join(seg.transcript) or "(none)")

            response = call_with_retry(client, model, [video_part, prompt], cfg)
            look: DeepLook | None = response.parsed
            if look is None:
                raise RuntimeError(f"{seg.segment_id}: the model returned no valid JSON. "
                                   "Rerun to retry; finished segments are kept.")
            for obs in look.observations:  # clip time -> film time
                obs.at = round(seg.start + obs.at, 1)
            result = DeepLookResult(segment_id=seg.segment_id, start=seg.start, end=seg.end,
                                    model=model, **look.model_dump())
            out.write(result.model_dump_json() + "\n")
            out.flush()
            done[seg.segment_id] = result
            label = "software" if result.shows_software else "no software"
            print(f"{seg.segment_id} {seg.start:>7.1f}s  {label:<11} "
                  f"{len(result.observations)} obs  {'; '.join(result.capabilities)}")
            tracker.add(response.usage_metadata, item=seg.segment_id)

    print(f"Estimated spend this run: ${tracker.spent_usd:.4f}")
    return [done[s.segment_id] for s in manifest.segments if s.segment_id in done]
