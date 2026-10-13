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


Evidence = Literal["observed", "necessary", "plausible", "speculative"]


class EvidenceRef(BaseModel):
    """One moment in the film that supports a capability card."""

    segment_id: str
    at: float = Field(description="seconds from film start, copied from the observation")
    what_happens: str
    evidence: Evidence


class CapabilityCard(BaseModel):
    """One buildable software capability, described as a black box: behaviour, not internals."""

    card_id: str = Field(description="C01, C02, ... in the order given")
    name: str = Field(description="short product-style name")
    what_it_does: str = Field(description="observable behaviour in 1-3 sentences, no "
                                          "implementation details")
    how_people_use_it: str = Field(description="who interacts with it and how: voice, gesture, "
                                               "touch, automatic")
    evidence: list[EvidenceRef]
    strongest_evidence: Evidence
    feasibility: Literal["buildable_now", "buildable_with_effort", "research_needed",
                         "fiction_only"] = Field(
        description="buildable_now: a small team could ship it with today's tools; "
                    "buildable_with_effort: possible but hard or costly; research_needed: "
                    "open research problem; fiction_only: breaks known physics or computing")
    feasibility_reason: str
    real_world_analogues: list[str] = Field(description="existing products, libraries or "
                                                        "research you believe are similar; "
                                                        "unverified until the Grounder checks")
    hardware_needed: list[str] = Field(description="hardware beyond a normal PC or phone, if any")
    merged_from: list[str] = Field(description="the exact raw capability names this card covers")


class DroppedCapability(BaseModel):
    """A raw capability that became no card, and why: keeps every input accounted for."""

    name: str
    reason: str


class CapabilityCards(BaseModel):
    """Output of Watcher Pass C."""

    cards: list[CapabilityCard]
    dropped: list[DroppedCapability]


class VisionBrief(BaseModel):
    """Your intended use for one capability, read from vision_<name>.md. No model writes this."""

    title: str
    who_uses_it: str
    use_case: str
    where_it_runs: str
    must_haves: list[str] = Field(min_length=1, max_length=5)
    out_of_scope: list[str]
    constraints: list[str]
    success_measure: str = Field(description="the 'How I'll know it works' line")


class GateSelection(BaseModel):
    """One card you picked, with the brief that says how you want it used."""

    card: CapabilityCard
    brief: VisionBrief
    brief_file: str


class GateResult(BaseModel):
    """Output of the human gate: what the Architect will plan for."""

    selections: list[GateSelection] = Field(min_length=1, max_length=3)


class Component(BaseModel):
    """One part of the system the Architect proposes."""

    component_id: str = Field(description="A1, A2, ...")
    name: str
    responsibility: str = Field(description="what this part does, in one or two sentences")
    technology: str = Field(description="the proposed language, library or service; the "
                                        "Grounder verifies it later")
    cloud_service: str = Field(description="managed cloud service it runs on, or 'none' "
                                           "if it runs on the user's device")
    serves_cards: list[str] = Field(description="card ids this component helps deliver")
    serves_must_haves: list[str] = Field(description="must-haves copied word for word from "
                                                     "the Vision Brief")


class DataFlow(BaseModel):
    """Data moving from one component to another."""

    source: str = Field(description="component_id, or 'user'")
    target: str = Field(description="component_id, or 'user'")
    data: str = Field(description="what moves, e.g. 'audio chunks, 16 kHz PCM'")
    protocol: str = Field(description="e.g. HTTPS, WebSocket, SQS message, function call")


class AdrOption(BaseModel):
    option: str
    pros: list[str]
    cons: list[str]


class Adr(BaseModel):
    """Architecture Decision Record: one choice, the options weighed, and why."""

    adr_id: str = Field(description="ADR-001, ADR-002, ...")
    title: str
    context: str
    options: list[AdrOption] = Field(description="at least two real options")
    decision: str
    consequences: str


class Conflict(BaseModel):
    """Where the film's evidence and the Vision Brief disagree. Only the user resolves it."""

    film_shows: str
    brief_says: str
    question_for_user: str


class Risk(BaseModel):
    risk: str
    likelihood: Literal["low", "medium", "high"]
    mitigation: str


class Architecture(BaseModel):
    """Output of the Architect."""

    summary: str = Field(description="the whole design in 3-5 sentences")
    components: list[Component]
    data_flows: list[DataFlow]
    adrs: list[Adr]
    conflicts: list[Conflict] = Field(description="empty if the film and the brief agree")
    assumptions: list[str]
    risks: list[Risk]


class Task(BaseModel):
    """One unit of work a junior engineer can finish and verify in a day or less."""

    task_id: str = Field(description="T01, T02, ...")
    title: str
    description: str = Field(description="what to build or set up, in 2-4 sentences")
    component_ids: list[str] = Field(description="architecture component ids this task builds; "
                                                 "empty only for project setup tasks")
    must_haves: list[str] = Field(description="must-haves this task delivers, copied word for "
                                              "word from the Vision Brief")
    depends_on: list[str] = Field(description="task ids that must be done first")
    files: list[str] = Field(description="repo paths this task creates or changes")
    done_when: str = Field(description="one observable result that proves the task is done")
    size: Literal["S", "M", "L"] = Field(description="S: under 2 h, M: 2-4 h, L: 4-8 h")
    paid_resource: str = Field(description="paid cloud resource this task creates and its "
                                           "estimated monthly cost, or 'none'")


class Milestone(BaseModel):
    milestone_id: str = Field(description="M1, M2, ...")
    title: str
    goal: str = Field(description="what works at the end of this milestone, in one sentence")
    tasks: list[Task]


class BuildPlan(BaseModel):
    """Output of the Lead Developer."""

    summary: str = Field(description="the build order and why, in 3-5 sentences")
    repo_layout: list[str] = Field(description="top-level folders and key files, one per line "
                                               "with a short purpose")
    milestones: list[Milestone]
    teardown: list[str] = Field(description="ordered steps that remove every cloud resource "
                                            "the plan creates")


class AcceptanceTest(BaseModel):
    """One test that proves a task or must-have works."""

    test_id: str = Field(description="AT01, AT02, ...")
    task_ids: list[str] = Field(description="tasks this test verifies")
    must_haves: list[str] = Field(description="must-haves it proves, copied word for word")
    checks_success_measure: bool = Field(description="true if it proves the brief's "
                                                     "'how I'll know it works'")
    kind: Literal["unit", "integration", "end_to_end", "manual"]
    given: str
    when: str
    then: str = Field(description="a measurable result, with a number where one fits")
    how_to_run: str = Field(description="exact command, or numbered manual steps")
    tool: str = Field(description="test tool, e.g. Vitest, Playwright, pytest, manual")


class AcceptancePlan(BaseModel):
    """Output of the Test Engineer."""

    strategy: str = Field(description="what is tested at which level and why, in 3-5 sentences")
    tests: list[AcceptanceTest]
    ci_steps: list[str] = Field(description="ordered GitHub Actions steps that run the "
                                            "automated tests")


# --- War Room: review round ---------------------------------------------------

Severity = Literal["blocker", "major", "minor"]


class ObjectionDraft(BaseModel):
    """One problem a reviewer found, as the reviewer writes it."""

    section: str = Field(description="the id it is about (A3, T07, AT05, ADR-002) or 'brief', "
                                     "'architecture', 'tasks', 'tests'")
    severity: Severity = Field(description="blocker: the plan fails the brief or cannot work; "
                                           "major: a real gap to fix; minor: an improvement")
    issue: str
    evidence: str = Field(description="what in the plan, brief or film shows the problem")
    proposed_fix: str


class Objection(ObjectionDraft):
    """An objection with its id and author, as stored in the debate log."""

    objection_id: str = Field(description="SK01, GR01, CO01, ...")
    reviewer: Literal["skeptic", "grounder", "cost_ops"]


class RubricCheck(BaseModel):
    """A reviewer cannot just say 'looks good': each rubric item is passed or failed, with why."""

    item: str
    passed: bool
    reason: str


class Verdict(BaseModel):
    """A reviewer's check of one of its own earlier objections after the authors revised.
    Only the reviewer who raised an objection can close it."""

    objection_id: str
    closed: bool
    reason: str


class Review(BaseModel):
    """What the Skeptic returns."""

    objections: list[ObjectionDraft] = Field(description="new problems only")
    checks: list[RubricCheck] = Field(description="one entry per rubric item")
    verdicts: list[Verdict] = Field(default_factory=list,
                                      description="one per earlier objection you raised, "
                                                  "if any were listed")


class CostReview(Review):
    """What the Cost/Ops Reviewer returns."""

    monthly_cost_usd: float = Field(description="estimated monthly cloud cost once deployed, "
                                                "after free tiers")
    cost_breakdown: list[str] = Field(description="one line per paid or free-tier resource: "
                                                  "service, usage assumed, monthly cost")


class Dependency(BaseModel):
    """A package or repository the plan relies on, as named in a public registry."""

    ecosystem: Literal["npm", "pypi", "github"]
    name: str = Field(description="exact registry name, e.g. '@huggingface/transformers', "
                                  "'fastapi', or 'owner/repo' for github")
    used_for: str
    component_ids: list[str]


class DependencyList(BaseModel):
    """What the Grounder's model extracts from the plan. Code then checks each one."""

    dependencies: list[Dependency]
    not_checkable: list[str] = Field(description="named technologies with no public registry "
                                                 "entry, e.g. AWS services or browser APIs")


class Grounding(BaseModel):
    """One registry lookup, done by code, not by a model."""

    ecosystem: str
    name: str
    status: Literal["ok", "missing", "archived", "stale", "license", "error"]
    latest_version: str = ""
    last_release: str = Field("", description="YYYY-MM-DD")
    license: str = ""
    note: str = ""


class ReviewRound(BaseModel):
    """Everything one War Room review round produced."""

    round: int
    objections: list[Objection] = Field(description="raised this round")
    verdicts: list[Verdict] = Field(default_factory=list,
                                      description="raisers' checks of earlier objections")
    carried: list[Objection] = Field(default_factory=list,
                                     description="earlier objections still open")
    checks: dict[str, list[RubricCheck]]
    grounding: list[Grounding]
    not_checkable: list[str]
    monthly_cost_usd: float
    cost_breakdown: list[str]


# --- War Room: Chairman and revisions ----------------------------------------

Owner = Literal["architect", "lead_dev", "test_eng", "none"]


class Ruling(BaseModel):
    """The Chairman's decision on one objection. The Chairman never writes plan content."""

    objection_id: str
    decision: Literal["accept", "reject", "ask_user"]
    owner: Owner = Field(description="who fixes it if accepted: architect (components, ADRs, "
                                     "data flows, technology choices), lead_dev (tasks, "
                                     "milestones, teardown, paid resources) or test_eng "
                                     "(tests, CI); 'none' otherwise")
    reason: str = Field(description="one line")
    question_for_user: str = Field("", description="only for ask_user: one question the user "
                                                   "can answer in a word or a sentence")


class Triage(BaseModel):
    rulings: list[Ruling]


class LedgerEntry(BaseModel):
    """One objection's whole life: raised, ruled on, fixed or not, closed by its raiser."""

    objection: Objection
    round_raised: int
    status: Literal["new", "open", "closed", "rejected", "ask_user"] = "new"
    owner: Owner = "none"
    ruling_reason: str = ""
    question_for_user: str = ""
    closed_reason: str = ""
