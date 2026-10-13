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

    p_cards = sub.add_parser("cards", help="Watcher Pass C: merge deep-look results into cards")
    p_cards.add_argument("run_dir", type=Path, help="folder with deep.jsonl")

    p_gate = sub.add_parser("gate", help="human gate: pick cards and attach your Vision Briefs")
    p_gate.add_argument("run_dir", type=Path, help="folder with cards.json")
    p_gate.add_argument("--pick", nargs=2, action="append", required=True,
                        metavar=("CARD_ID", "BRIEF_FILE"),
                        help="a card id and its Vision Brief file; repeat for up to 3 cards")

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
    elif args.command == "cards":
        from movietech.watcher_cards import make_cards

        cards, warnings = make_cards(args.run_dir)
        for c in cards.cards:
            print(f"{c.card_id}  {c.feasibility:<22} {c.strongest_evidence:<11} {c.name}")
        for w in warnings:
            print(f"Warning: {w}")
        print(f"{len(cards.cards)} cards, {len(cards.dropped)} dropped. "
              f"Read {args.run_dir / 'cards.md'}")
    elif args.command == "gate":
        from movietech.gate import BriefError, run_gate

        try:
            result = run_gate(args.run_dir, [(c, Path(b)) for c, b in args.pick])
        except BriefError as e:
            raise SystemExit(f"Not accepted:\n{e}") from None
        for s in result.selections:
            print(f"{s.card.card_id}  {s.card.name}  <-  {s.brief_file} "
                  f"({len(s.brief.must_haves)} must-haves)")
        print(f"Accepted. Wrote {args.run_dir / 'gate.json'}")
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
