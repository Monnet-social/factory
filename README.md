# factory (Monnet-social/factory)

Agent assets for Monnett repositories. First product: an AI first-pass reviewer for
`monnett-core` pull requests on Bitbucket, run as a Claude Code routine.
Design and decisions: `monnett-team/wiki/ai-code-review.md`, ADR 0003.

| Path | Content |
|---|---|
| `.claude/skills/monnett-core-review/` | Review skill: procedure, rubric (from 400 human review comments), known CLAUDE.md drift, output schema |
| `checks/precheck.py` | Deterministic diff-scoped checks: Flyway collisions/idempotency, endpoint security/OpenAPI, Bruno, `.env.example`, AppConfigTest, internal mocks, test `@Transactional`, layering imports, nested types, listener catch, comment length |
| `bitbucket/bb.py` | Routine side: `prepare` (validate payload, clone, diff, pre-checks, Jira → workspace), `post` (inline + summary comments, idempotent per SHA; replies to and resolves earlier findings the reviewer verified as fixed), `feedback` (votes, replies and fixes per AI comment, with totals) |
| `jira/jira_issue.py` | Jira issue → Markdown (description, AC) |
| `routine/PROMPT.md` | Routine prompt (API trigger, shadow/live mode via `REVIEW_MODE`) |
| `routine/pull-requests-step.yml` | Bitbucket pipeline step that fires the routine |
| `relay/` | Cloudflare Worker: Bitbucket webhook (signed) → routine `/fire`, once per PR head SHA; preferred trigger, no pipeline change |
| `backtest/` | Replay the reviewer on historical PRs at their first-review commit and score it against human comments |

## Workspace contract
`bb.py prepare` (live) and `backtest/prepare.py` (replay) produce the same layout, which the skill reads:
`pr/{meta.json, diff.patch, files.txt, precheck.json, jira.md, prior-review.json}` + `core/` (repo at PR head).
`prior-review.json` (earlier AI findings with developer replies) exists only in live runs.

## Routine setup (pilot)
1. Push this repo to GitHub; create the routine at claude.ai/code/routines with this repo, prompt
   `routine/PROMPT.md`, model Opus, API trigger.
2. Environment: network Custom = `bitbucket.org`, `api.bitbucket.org`, `monnett.atlassian.net`;
   variables `BB_TOKEN` (repository read + pull request write), `JIRA_EMAIL`, `JIRA_TOKEN`,
   `REVIEW_MODE=shadow`. On Pro/Max store tokens as API credentials instead.
3. In monnett-core: add `routine/pull-requests-step.yml` and secured variables
   `ROUTINE_FIRE_URL`, `ROUTINE_FIRE_TOKEN`.

## Backtest
```bash
python3 backtest/select_prs.py --raw <bitbucket dump> --repo <core clone> --out sel.json
JIRA_EMAIL=… JIRA_TOKEN=… python3 backtest/prepare.py --selection sel.json --repo <core clone> --runs runs/
backtest/run_reviews.sh runs/ 4 opus
backtest/run_judges.sh runs/ sel.json <core clone> 4 opus
python3 backtest/score.py --runs runs/ --selection sel.json --out report.json
```
Keep `runs/` and `sel.json` outside this repo: they contain colleagues' review comments.
