# movietech-reveng

A multi-agent pipeline that watches a film, reverse-engineers the fictional software it shows, and produces a tested, executable project plan.

**Status:** Step 2 of the build: the Ingestor and Watcher Pass A (triage).

```
video ──► Ingestor (ffmpeg + PySceneDetect + subtitles) ──► manifest.json
                                                             │
                     Watcher Pass A (Gemini Flash triage) ◄──┘──► triage.jsonl
```

| Stage | What it does | Uses an LLM? |
|---|---|---|
| Ingestor | Cuts the film into 20–90 s segments, saves 3 keyframes each, attaches subtitle lines | No |
| Watcher Pass A | Asks a cheap vision model, for each segment: "does a software system act here?" | Yes (`gemini-3.8-flash`) |

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
  cli.py             python -m movietech ...
tests/               pytest; Gemini is faked so CI needs no keys
```

## Copyright
Only process films you have the rights to. The public demo uses *Tears of Steel* (© Blender Foundation, CC BY 3.0, mango.blender.org). `input/` and `output/` are git-ignored, so frames from copyrighted films never reach this repo.
