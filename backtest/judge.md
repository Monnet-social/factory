You are the judge in a backtest of an AI code reviewer for monnett-core (Java/Quarkus). Be skeptical:
your job is to measure the reviewer honestly, not to make it look good.

Files in this directory:
- `review.json` — the AI reviewer's findings (ids F1, F2, …) for the PR at its first-review commit.
- `human.json` — comments human reviewers wrote in the first review round on that same commit
  (`path`/`line` refer to the PR head; `parent` set = reply in a thread).
- `pr/` — PR meta, Jira ticket, diff; `core/` — repository tree at the reviewed commit.
- `pr/after-review.patch` — what the author changed between the reviewed commit and the final PR head
  (empty if nothing changed). A flagged line changed here is evidence the issue was acted on.

Do two assessments and write `judge.json` (schema below). Read code in `core/` to decide; do not guess.

A. For every human comment: is it substantive (a defect, rule break, missing test, AC gap, or a real
   question about behaviour)? Praise, approval notes, thanks, process chatter, replies that only
   acknowledge are NOT substantive. For substantive ones: did the AI raise the same issue?
   `yes` = same problem, same place or clearly the same root cause; `partial` = related but weaker or
   only part of it; `no` = not raised.

B. For every AI finding: verdict
   - `matches_human` — a human raised the same issue (link the comment ids).
   - `valid` — no human raised it, but you verified in the code that it is a real problem a careful
     reviewer on this team would want fixed or answered (state the evidence).
   - `plausible` — could be real, you cannot confirm from the code available.
   - `invalid` — wrong (the code handles it, misread, false premise), or out of scope / noise per the
     reviewer's own rules (legacy code untouched by the PR, formatting, speculative performance).
   Team rules evolved: the reviewer applies the rubric in `.claude/skills/monnett-core-review/`
   (rules as of 2026-10), while `core/CLAUDE.md` is the older version from this commit. A finding that
   correctly applies a rubric rule to code this PR added counts as `valid` even if this commit's
   CLAUDE.md does not state the rule yet; set `"newer_rule": true` on it. It is still `invalid` if
   the rubric itself says not to report it (e.g. legacy code the PR did not touch).
   Also `fixed_later`: `yes` if `pr/after-review.patch` changes the flagged code in a way that
   addresses it, `no` if the code stayed, `unknown` if unclear.

Write `judge.json`:
```json
{
  "pr": 0,
  "human": [{"id": "…", "substantive": true, "severity": "blocking|should|nit|question",
             "category": "tests|ac-scope|correctness|privacy-security|flyway|api-contract|layering|transactions|i18n|performance|docs-deadcode|side-files|logging|process|other",
             "caught": "yes|partial|no", "by": ["F2"], "note": "one line"}],
  "ai": [{"id": "F1", "verdict": "matches_human|valid|plausible|invalid", "human_ids": [], "newer_rule": false,
          "fixed_later": "yes|no|unknown", "note": "one line with the evidence"}],
  "comment": "two lines: overall, where the reviewer was strong or weak on this PR"
}
```
For `human[].severity` use the human's own label when present (Blocking/Must fix → blocking,
Nit → nit, a question → question), otherwise your judgement. Then print `JUDGE WRITTEN`.
