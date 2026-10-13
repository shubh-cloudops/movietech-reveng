# Vision Brief: <capability name>

Fill in one of these per capability you pick at the human gate (after the Watcher, before the Architect).
Save each one as `C:\movietech\input\vision_<short-name>.md`, for example `vision_jarvis.md`.
Keep the headings exactly as written; the pipeline reads them.

## Who uses it
<!-- One line. e.g. "Me, a cloud technician, running my home lab." -->

## Use case
<!-- 1 to 3 sentences: the problem it solves and when you'd reach for it. -->

## Where it runs
<!-- Pick: web / desktop / mobile / CLI / voice / other (say which). -->

## Must-haves
<!-- Up to 5 bullet points, one must-have per bullet. The Architect must cover every one and the Test Engineer must prove it, so add a number where you can, e.g. "captions within 2 seconds". -->
-
-

## Out of scope
<!-- What it must NOT do or include. The Skeptic blocks plans that build these. -->
-

## Constraints
<!-- Budget, cloud (default: AWS, free tier first), privacy, devices, anything fixed. -->
-

## How I'll know it works
<!-- One measurable line. e.g. "Answers a voice question about my AWS bill in under 3 seconds." -->

---

### Example (made up)

# Vision Brief: JARVIS-style assistant

## Who uses it
Me, a cloud technician, at my desk.

## Use case
I ask out loud about the state of my AWS account (costs, running instances, alarms) and get a short spoken answer, so I can check things without opening the console.

## Where it runs
Desktop (Windows), voice in and voice out.

## Must-haves
- Wake word or push-to-talk
- Answers questions about AWS cost, EC2 status and CloudWatch alarms
- Read-only: never changes anything in AWS
- Spoken answer plus a text log

## Out of scope
- Controlling smart-home devices
- Making any change to AWS resources

## Constraints
- Under $5/month to run
- AWS free tier where possible
- No credentials in code

## How I'll know it works
"What did I spend this week?" gets a correct spoken answer in under 3 seconds.
