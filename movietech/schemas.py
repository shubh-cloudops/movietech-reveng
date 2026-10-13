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


class Observation(BaseModel):
    """One moment where the system does something, tagged with how sure we are."""

    at: float = Field(description="seconds from the start of the clip")
    what_happens: str = Field(description="what the system does, in one or two sentences")
    interface: str = Field(description="how the system shows itself: screen, HUD, hologram, "
                                       "voice, physical motion, or none")
    user_input: Literal["voice", "gesture", "touch", "gaze", "keyboard", "automatic",
                        "none", "other"]
    evidence: Literal["observed", "necessary", "plausible", "speculative"] = Field(
        description="observed: seen or heard on screen; necessary: must exist for what is seen "
                    "to work; plausible: likely but not shown; speculative: a guess")


class DeepLook(BaseModel):
    """What the deep-look model must return for one clip."""

    shows_software: bool = Field(description="false if the clip only shows hardware, props "
                                             "or credits with no software behaviour")
    summary: str = Field(description="two or three sentences on what the software does here")
    observations: list[Observation]
    capabilities: list[str] = Field(description="short names of software capabilities a real "
                                                "team could build from this, e.g. 'live 3D "
                                                "wireframe scan of a person'")


class DeepLookResult(DeepLook):
    """A deep look tied back to its segment. Here `at` is seconds from film start."""

    segment_id: str
    start: float
    end: float
    model: str
