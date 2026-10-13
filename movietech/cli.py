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

    p_deep = sub.add_parser("deep", help="Watcher Pass B: deep look at flagged segments")
    p_deep.add_argument("run_dir", type=Path, help="folder created by ingest and triage")
    p_deep.add_argument("--video", type=Path, required=True, help="the same video given to ingest")

    p_costs = sub.add_parser("costs", help="show estimated spend per stage for a run")
    p_costs.add_argument("run_dir", type=Path)

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
    elif args.command == "deep":
        from movietech.watcher_deep import deep_look

        results = deep_look(args.run_dir, args.video)
        real = [r for r in results if r.shows_software]
        caps = sum(len(r.capabilities) for r in real)
        print(f"{len(real)} of {len(results)} flagged segments show software; "
              f"{caps} capabilities named. Wrote {args.run_dir / 'deep.jsonl'}")
    elif args.command == "costs":
        from movietech.costs import summarise

        totals = summarise(args.run_dir)
        if not totals:
            print("No costs.jsonl yet. Costs are logged from this version on.")
        for stage, t in totals.items():
            print(f"{stage:<8} {int(t['calls']):>4} calls  in {int(t['input_tokens']):>9,}  "
                  f"out {int(t['output_tokens']):>7,}  thinking {int(t['thinking_tokens']):>7,}  "
                  f"${t['usd']:.4f}")
        if totals:
            print(f"{'total':<8} ${sum(t['usd'] for t in totals.values()):.4f}")
