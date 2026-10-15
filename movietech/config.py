"""Central settings.

Model IDs and prices live here, not in agent code, so switching a model is a one-line change.
Any value can be overridden with an environment variable.
"""

import os

# --- Models -----------------------------------------------------------------
TRIAGE_MODEL = os.getenv("MOVIETECH_TRIAGE_MODEL", "gemini-3.8-flash")
DEEP_MODEL = os.getenv("MOVIETECH_DEEP_MODEL", "gemini-3.1-pro-preview")
# Gemini 3 thinking level for Pass B: low / medium / high. Thinking tokens bill as output.
DEEP_THINKING = os.getenv("MOVIETECH_DEEP_THINKING", "medium")
CARDS_MODEL = os.getenv("MOVIETECH_CARDS_MODEL", "claude-opus-5-5")
# Claude effort for Pass C: low / medium / high / xhigh / max. Thinking bills as output.
CARDS_EFFORT = os.getenv("MOVIETECH_CARDS_EFFORT", "high")
CARDS_MAX_TOKENS = 32000  # hard ceiling on one Pass C answer: at most $0.64 of output on Opus
ARCHITECT_MODEL = os.getenv("MOVIETECH_ARCHITECT_MODEL", "claude-opus-5-5")
ARCHITECT_EFFORT = os.getenv("MOVIETECH_ARCHITECT_EFFORT", "high")
ARCHITECT_MAX_TOKENS = 32000
LEAD_DEV_MODEL = os.getenv("MOVIETECH_LEAD_DEV_MODEL", "claude-opus-5-5")
LEAD_DEV_EFFORT = os.getenv("MOVIETECH_LEAD_DEV_EFFORT", "high")
LEAD_DEV_MAX_TOKENS = 32000
TEST_ENG_MODEL = os.getenv("MOVIETECH_TEST_ENG_MODEL", "claude-opus-5-5")
TEST_ENG_EFFORT = os.getenv("MOVIETECH_TEST_ENG_EFFORT", "high")
TEST_ENG_MAX_TOKENS = 32000
# War Room reviewers. The Skeptic is a different model family from the authors on purpose.
SKEPTIC_MODEL = os.getenv("MOVIETECH_SKEPTIC_MODEL", "gemini-3.1-pro-preview")
SKEPTIC_THINKING = os.getenv("MOVIETECH_SKEPTIC_THINKING", "high")
SKEPTIC_MAX_TOKENS = 32768
GROUNDER_MODEL = os.getenv("MOVIETECH_GROUNDER_MODEL", "claude-haiku-5-5")
COST_OPS_MODEL = os.getenv("MOVIETECH_COST_OPS_MODEL", "claude-haiku-5-5")
REVIEW_EFFORT = os.getenv("MOVIETECH_REVIEW_EFFORT", "high")  # Grounder and Cost/Ops
REVIEW_MAX_TOKENS = 32000  # a 35-task plan overflowed 16K; at most $0.02 of output on Haiku
CHAIRMAN_MODEL = os.getenv("MOVIETECH_CHAIRMAN_MODEL", "claude-opus-5-5")
CHAIRMAN_EFFORT = os.getenv("MOVIETECH_CHAIRMAN_EFFORT", "high")
CHAIRMAN_MAX_TOKENS = 32000  # room to rule on many objections; at most $0.64 on Opus
REVISE_EFFORT = os.getenv("MOVIETECH_REVISE_EFFORT", "medium")  # authors fixing objections
WARROOM_MAX_ROUNDS = 3
# Server-side refusal fallback is offered on these models only (not on Haiku).
SERVER_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}

# USD per 1M tokens: (input, output). Thinking tokens bill as output.
# Source: ai.google.dev/gemini-api/docs/pricing, checked 2026-10-12.
PRICES_USD_PER_M = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-2.5-pro": (1.25, 10.00),
    # Source: platform.claude.com pricing, checked 2026-10-13.
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),  # prompts up to 100K tokens; ours stay well under
}
# Used when a response comes from a model not listed above (e.g. a safety fallback):
# the most expensive current Claude tier, so the estimate errs high.
UNKNOWN_MODEL_PRICE = (10.00, 50.00)

# --- Cost guard -------------------------------------------------------------
# A run stops once its estimated spend passes this. Google budgets only alert,
# so this is the real brake.
MAX_RUN_USD = float(os.getenv("MOVIETECH_MAX_RUN_USD", "1.00"))
# Pass B sends real video to a Pro model, so it gets its own, higher cap.
DEEP_MAX_RUN_USD = float(os.getenv("MOVIETECH_DEEP_MAX_USD", "3.00"))
CARDS_MAX_RUN_USD = float(os.getenv("MOVIETECH_CARDS_MAX_USD", "2.00"))
ARCHITECT_MAX_RUN_USD = float(os.getenv("MOVIETECH_ARCHITECT_MAX_USD", "2.00"))
LEAD_DEV_MAX_RUN_USD = float(os.getenv("MOVIETECH_LEAD_DEV_MAX_USD", "2.00"))
TEST_ENG_MAX_RUN_USD = float(os.getenv("MOVIETECH_TEST_ENG_MAX_USD", "2.00"))
REVIEW_MAX_RUN_USD = float(os.getenv("MOVIETECH_REVIEW_MAX_USD", "1.50"))  # one whole round
WARROOM_MAX_RUN_USD = float(os.getenv("MOVIETECH_WARROOM_MAX_USD", "5.00"))  # all 3 rounds

# --- Grounder ---------------------------------------------------------------
STALE_AFTER_DAYS = 730  # no release for 2 years counts as unmaintained
OK_LICENSES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD",
               "MPL-2.0", "Unlicense", "PSF-2.0", "Python-2.0", "BlueOak-1.0.0"}

# --- Ingest -----------------------------------------------------------------
SEGMENT_MIN_S = 20.0  # merge short shots until a segment is at least this long
SEGMENT_MAX_S = 90.0  # never let a segment grow past this
KEYFRAMES_PER_SEGMENT = 3
KEYFRAME_WIDTH = 640  # pixels; low-res is enough for triage and keeps tokens down

# --- Pass B clips ---------------------------------------------------------
CLIP_HEIGHT = 480  # pixels; Gemini downsamples video frames anyway
CLIP_MAX_BITRATE = "1M"  # keeps a 90s clip near 12MB, under the 20MB inline request limit
DEEP_FPS = float(os.getenv("MOVIETECH_DEEP_FPS", "1"))  # frames per second Gemini samples
