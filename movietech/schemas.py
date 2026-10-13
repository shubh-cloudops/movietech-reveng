"""Data contracts between pipeline stages. Every stage reads and writes these, never raw dicts."""

from typing import Literal

from pydantic import BaseModel, Field


class Segment(BaseModel):
    """A run of consecutive shots, long enough to judge on its own."""

    segment_id: str
    start: float = Field(description="seconds from film start")
    end: float
    shot_count: int
    keyframes: list[str] = Field(description="image paths relative to the manifest folder")
    transcript: list[str] = Field(description="subtitle lines spoken inside this segment")


class Manifest(BaseModel):
    """Output of the Ingestor: the whole film as timestamped segments."""

    source: str
    source_mode: Literal["file"] = "file"
    duration: float
    segments: list[Segment]


class TriageVerdict(BaseModel):
    """What the triage model must return. Kept tiny so the cheap model answers reliably."""

    verdict: Literal["yes", "no", "unsure"]
    reason: str = Field(description="one line naming what was seen or heard")


class TriageResult(TriageVerdict):
    """A verdict tied back to its segment and timestamps."""

    segment_id: str
    start: float
    end: float
