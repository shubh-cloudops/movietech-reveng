# movietech-reveng

A multi-agent pipeline that watches a film, reverse-engineers the fictional software it shows, and produces a tested, executable project plan.

**Status:** Step 3 of the build: the Ingestor, Watcher Pass A (triage) and Pass B (deep look).

```
video ──► Ingestor (ffmpeg + PySceneDetect + subtitles) ──► manifest.json
                                                             │
                     Watcher Pass A (Gemini Flash triage) ◄──┘──► triage.jsonl
                                                             │ yes / unsure only
                     Watcher Pass B (Gemini Pro, real video) ◄┘──► deep.jsonl
                                                                   costs.jsonl (every call)
```

| Stage | What it does | Uses an LLM? |
|---|---|---|
| Ingestor | Cuts the film into 20–90 s segments, saves 3 keyframes each, attaches subtitle lines | No |
| Watcher Pass A | Asks a cheap vision model, for each segment: "does a software system act here?" | Yes (`gemini-3.8-flash`) |
| Watcher Pass B | Sends a small video clip of each flagged segment to a Pro model, which lists timestamped observations (tagged observed / necessary / plausible / speculative) and the capabilities a real team could build | Yes (`gemini-3.1-pro-preview`) |

Why the split: the cheap, repeatable work is plain code, and only the judgement goes to a model. A 2-hour film shrinks to the 20–40 segments worth an expensive look.

---

## Run it on Windows 11 (PowerShell)

Prerequisites from step 1: Python 3.12, Git, ffmpeg, a `GEMINI_API_KEY` user environment variable, and `C:\movietech\input\tears_of_steel.mkv`.

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

**6. See where the money went (free)**
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
  costs.py           cost cap and per-call ledger (costs.jsonl)
  gemini.py          retry helper for Gemini calls
  cli.py             python -m movietech ...
tests/               pytest; Gemini is faked so CI needs no keys
```

## Copyright
Only process films you have the rights to. The public demo uses *Tears of Steel* (© Blender Foundation, CC BY 3.0, mango.blender.org). `input/` and `output/` are git-ignored, so frames from copyrighted films never reach this repo.
