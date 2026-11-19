# User tests

The pitch has to show: this problem, for this user, we built this, real users tried it, we changed it, here is the result.
This file is where each round is recorded. Roles only, no names. No SECO plan content.

## Who we test with

| Profile | Why them | Where to find one |
|---|---|---|
| Structural or MEP engineer (main user) | SECO's engineers do the control work; the spec says "an engineer who is not a BIM modeller" | SECO via Martin; engineering students or staff at Uni.lu |
| Architect or BIM modeller | knows what a correct model looks like; spots wrong walls fast | architecture students, BIM course |
| Outsider (week-2 rule) | finds what is unclear to someone with no context | anyone, 10 minutes |

## Setup (5 minutes)

1. Start the try-it page: `python -m dxf2ifc serve --out out/web`, open `http://127.0.0.1:8000`.
2. Upload a plan the tester has not seen. Use a public or synthetic one for outsiders; SECO plans only with SECO people.
3. Open the review page from the result. Keep the 3D view in a second tab.

## Session (10 minutes)

1. Say only: "A program turned this 2D drawing into a 3D building model. Find what you would not trust."
2. Do not help. Note where they hesitate, what they click, what they say out loud.
3. They mark items Correct or Wrong on the review page, then fill in the feedback form at the bottom.
4. Ask: What did you check first? What was unclear? Would you use this instead of redrawing? What would make you trust it?

Answers are saved by the try-it server in `out/web/feedback.jsonl` (or downloaded as `feedback-*.json` when the page
is opened as a file). Sum them up with:

```
python -m dxf2ifc feedback out/web/feedback.jsonl
```

## Rounds

Fill one block per round. A round ends with one change made and committed.

### Round 0: outsider test (week 2)

- Date:
- Tester profile:
- Plan used:
- Minutes to first finding:
- Usefulness (1-5):
- Three findings:
  1.
  2.
  3.
- Change made (commit):

### Round 1: engineer

- Date:
- Tester profile:
- Plan used:
- Minutes to first finding:
- Usefulness (1-5):
- Items judged correct / wrong:
- Three findings:
  1.
  2.
  3.
- Change made (commit):

### Round 2: after the changes

Same questions, ideally the same tester and a new plan, to show the change helped.

## For the pitch

One slide: profile, what they said, what we changed, the number before and after (usefulness, wrong items, minutes to first finding).
