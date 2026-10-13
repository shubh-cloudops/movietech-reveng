# movietech-reveng

A multi-agent pipeline that watches a film, reverse-engineers the fictional software it shows, and produces a tested, executable project plan.

**Status:** Step 6 of the build: the Ingestor, Watcher Passes A–C, the human gate and the Architect.

```
video ──► Ingestor (ffmpeg + PySceneDetect + subtitles) ──► manifest.json
                                                             │
                     Watcher Pass A (Gemini Flash triage) ◄──┘──► triage.jsonl
                                                             │ yes / unsure only
                     Watcher Pass B (Gemini Pro, real video) ◄┘──► deep.jsonl
                                                             │
                     Watcher Pass C (Claude Opus)           ◄┘──► cards.json + cards.md
                                                             │
                     Human gate (you: 1-3 cards + Vision Briefs) ◄┘──► gate.json
                                                             │
                     Architect (Claude Opus)                ◄┘──► plan/architecture.json + .md
                                                                   costs.jsonl (every call)
```

| Stage | What it does | Uses an LLM? |
|---|---|---|
| Ingestor | Cuts the film into 20–90 s segments, saves 3 keyframes each, attaches subtitle lines | No |
| Watcher Pass A | Asks a cheap vision model, for each segment: "does a software system act here?" | Yes (`gemini-3.8-flash`) |
| Watcher Pass B | Sends a small video clip of each flagged segment to a Pro model, which lists timestamped observations (tagged observed / necessary / plausible / speculative) and the capabilities a real team could build | Yes (`gemini-3.1-pro-preview`) |
| Watcher Pass C | Merges the deep-look notes into a short list of capability cards: behaviour, evidence, feasibility, real-world analogues. Every raw capability ends up in a card or in a "dropped" list with a reason | Yes (`claude-opus-5-5`) |
| Human gate | You pick 1–3 cards and write a Vision Brief for each: who uses it, where it runs, must-haves, out of scope, how you'll know it works. The code checks every brief and lists all problems at once | No |
| Architect | Designs components, data flow and one ADR per significant choice, serving every must-have and building nothing out of scope. Where the film and your brief disagree it asks you instead of choosing. Code then checks coverage | Yes (`claude-opus-5-5`) |

Why the split: the cheap, repeatable work is plain code, and only the judgement goes to a model. A 2-hour film shrinks to the 20–40 segments worth an expensive look.

---

## Run it on Windows 11 (PowerShell)

Prerequisites from step 1: Python 3.12, Git, ffmpeg, `GEMINI_API_KEY` and `ANTHROPIC_API_KEY` user environment variables, and `C:\movietech\input\tears_of_steel.mkv`.

**1. Clone and create a virtual environment**
```powershell
cd C:\movietech
git clone https://github.com/shubh-cloudops/movietech-reveng.git
cd movietech-reveng
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```
Expected result: the prompt starts with `(.venv)` and pip ends with "Successfully installed …".
If `Activate.ps1` is blocked ("running scripts is disabled"), run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, answer `Y`, and retry.

**2. Run the tests (free, no API calls)**
```powershell
pytest -q
```
Expected result: `9 passed`.

**3. Ingest the test film (free, local only)**
```powershell
python -m movietech ingest C:\movietech\input\tears_of_steel.mkv --out C:\movietech\output\tos
```
Add `--srt C:\movietech\input\tears_of_steel_en.srt` if you downloaded the subtitle file.
Expected result: a line like `N segments from 734s of video; M have dialogue`, and `C:\movietech\output\tos\keyframes\` full of JPEGs.
If you see `'ffprobe' is not recognized`, ffmpeg isn't on PATH: close and reopen PowerShell after installing it.

**4. Triage (paid: about $0.02–$0.10 for Tears of Steel, estimated)**
```powershell
python -m movietech triage C:\movietech\output\tos
```
Expected result: one line per segment (`S004   92.0s  yes    holographic display shows ...`), then the estimated spend.
- A run stops at **$1.00** by default. Change it with `$env:MOVIETECH_MAX_RUN_USD = "2.00"`.
- Running it again skips finished segments, so a stopped run resumes without paying twice.
- If you get a 404 for the model, set `$env:MOVIETECH_TRIAGE_MODEL = "gemini-2.5-pro"` and retry.

**5. Deep look (paid: about $0.50–$1.50 for Tears of Steel, estimated)**
```powershell
python -m movietech deep C:\movietech\output\tos --video C:\movietech\input\tears_of_steel.webm
```
Expected result: one line per flagged segment (`S002   25.0s  software    3 obs  live 3D wireframe scan; ...`), then the estimated spend.
- A run stops at **$3.00** by default. Change it with `$env:MOVIETECH_DEEP_MAX_USD = "5.00"`.
- Clips are saved in `C:\movietech\output\tos\clips\` and reused on rerun.
- If you get a 404 for the model, set `$env:MOVIETECH_DEEP_MODEL = "gemini-2.5-pro"` and retry.
- If you get a 400 about thinking level, set `$env:MOVIETECH_DEEP_THINKING = "high"` and retry.

**6. Capability cards (paid: about $0.20–$0.60, estimated; never more than about $0.70)**
```powershell
python -m movietech cards C:\movietech\output\tos
```
Expected result: one line per card (`C01  buildable_now  observed  Live 3D wireframe scanner`), then `Read C:\movietech\output\tos\cards.md`. Open `cards.md` to choose what to build.
- Before calling, it checks the worst case (a full 32,000-token answer) against a **$2.00** cap (`MOVIETECH_CARDS_MAX_USD`).
- `Warning:` lines mean the model left a capability unaccounted for or cited a segment that doesn't exist. Rerun, or tell us.
- A `401`/`authentication_error` means `ANTHROPIC_API_KEY` isn't set in this window.

**7. Human gate (free)**

Copy `templates\vision-brief-template.md` to `C:\movietech\input\vision_<name>.md`, fill it in for the card you picked, then:
```powershell
python -m movietech gate C:\movietech\output\tos --pick C06 C:\movietech\input\vision_captions.md
```
Add another `--pick CARD_ID FILE` for each extra card (up to 3).
Expected result: `C06  Live Spatial Speech Captions  <-  vision_captions.md (3 must-haves)` and `Accepted.`
- `Not accepted:` lists every problem in your brief; fix them and rerun.
- Keep the headings exactly as in the template. Everything after the `---` line (the made-up example) is ignored.

**8. Architect (paid: about $0.15–$0.40, estimated; never more than about $0.65)**
```powershell
python -m movietech architect C:\movietech\output\tos
```
Expected result: one line per component (`A1  Web client  React  [none]`), the number of ADRs, risks and questions for you, then `Read ...\plan\architecture.md`.
- `Problem:` lines come from the code check (for example a must-have no component serves). The War Room will make the Architect fix them; for now, tell us.
- Questions for you appear at the top of `architecture.md`.

**9. See where the money went (free)**
```powershell
python -m movietech costs C:\movietech\output\tos
```
Expected result: one line per stage with calls, tokens and dollars, then a total.

**Teardown**
```powershell
deactivate
Remove-Item -Recurse -Force C:\movietech\output\tos
```
API keys are removed as described in the step 1 checklist.

---

## Project layout
```
movietech/
  config.py          model IDs, prices, cost cap, segment sizes
  schemas.py         Pydantic contracts between stages
  ingest.py          Stage 0: Ingestor
  watcher_triage.py  Watcher Pass A
  watcher_deep.py    Watcher Pass B
  watcher_cards.py   Watcher Pass C
  gate.py            human gate: Vision Brief parser and card picks
  architect.py       Architect agent and its coverage check
  claude.py          one structured Claude call: budget pre-check, streaming, fallbacks
templates/           Vision Brief template you copy and fill in
  costs.py           cost cap and per-call ledger (costs.jsonl)
  gemini.py          retry helper for Gemini calls
  cli.py             python -m movietech ...
tests/               pytest; Gemini is faked so CI needs no keys
```

## Copyright
Only process films you have the rights to. The public demo uses *Tears of Steel* (© Blender Foundation, CC BY 3.0, mango.blender.org). `input/` and `output/` are git-ignored, so frames from copyrighted films never reach this repo.
