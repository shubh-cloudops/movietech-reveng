"""Command line entry point: python -m movietech <command> ..."""

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="movietech")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="split a video into segments, keyframes and subtitles")
    p_ingest.add_argument("video", type=Path)
    p_ingest.add_argument("--out", type=Path, required=True, help="run folder to create")
    p_ingest.add_argument("--srt", type=Path, help="sidecar subtitle file (optional)")

    p_triage = sub.add_parser("triage", help="Watcher Pass A: flag segments that show tech")
    p_triage.add_argument("run_dir", type=Path, help="folder created by ingest")

    args = parser.parse_args()
    if args.command == "ingest":
        from movietech.ingest import ingest

        m = ingest(args.video, args.out, args.srt)
        with_lines = sum(1 for s in m.segments if s.transcript)
        print(f"{len(m.segments)} segments from {m.duration:.0f}s of video; "
              f"{with_lines} have dialogue. Wrote {args.out / 'manifest.json'}")
    elif args.command == "triage":
        from movietech.watcher_triage import triage

        results = triage(args.run_dir)
        flagged = [r for r in results if r.verdict != "no"]
        print(f"{len(flagged)} of {len(results)} segments flagged yes/unsure. "
              f"Wrote {args.run_dir / 'triage.jsonl'}")
