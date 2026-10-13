# factory (Monnet-social/factory)

Agent assets for Monnett repositories. First product: an AI first-pass reviewer for
`monnett-core` and `monnett-app` pull requests on Bitbucket, one Claude Code routine per repo.
Design and decisions: `monnett-team/wiki/ai-code-review.md`, `ai-code-review-app.md`, ADR 0003.

| Path | Content |
|---|---|
| `.claude/skills/monnett-core-review/` | Review skill: procedure, rubric (from 400 human review comments), known CLAUDE.md drift, output schema |
| `.claude/skills/monnett-app-review/` | Same for the Flutter app (rubric from 255 comments); reads core `develop` for the API contract |
| `checks/precheck.py` | Deterministic diff-scoped checks: Flyway collisions/idempotency, endpoint security/OpenAPI, Bruno, `.env.example`, AppConfigTest, internal mocks, test `@Transactional`, layering imports, nested types, listener catch, comment length |
| `checks/precheck_app.py` | App pre-checks without the Flutter SDK: ARB parity/order/metadata, layer imports, flags, DTO tests/defaults, hardcoded strings, context after await, dispose, pubspec |
| `bitbucket/bb.py` | Routine side (`--repo`, per-repo config in `REPOS`, incl. a live gate): `prepare` (validate payload, clone, diff, pre-checks, Jira → workspace), `post` (inline + summary comments, idempotent per SHA; replies to and resolves earlier findings the reviewer verified as fixed), `feedback` (votes, replies and fixes per AI comment, with totals) |
| `jira/jira_issue.py` | Jira issue → Markdown (description, AC) |
| `routine/PROMPT.md`, `PROMPT-app.md` | Routine prompts (API trigger, shadow/live mode via `REVIEW_MODE` and the `REPOS` live gate) |
| `routine/pull-requests-step.yml` | Bitbucket pipeline step that fires the routine |
| `relay/` | Cloudflare Worker: Bitbucket webhook (signed) → the repo's routine `/fire` (`ROUTES`), once per PR head SHA; preferred trigger, no pipeline change |
| `backtest/` | Replay the reviewer on historical PRs at their first-review commit and score it against human comments |

## Workspace contract
`bb.py prepare` (live) and `backtest/prepare.py` (replay) produce the same layout, which the skill reads:
`pr/{meta.json, diff.patch, files.txt, precheck.json, jira.md, prior-review.json}` + `core/` or `app/`
(repo at PR head); app workspaces also get `core/` (monnett-core `develop`, read-only contract context).
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
# app: prepare.py --target app --repo <app clone> --core <core clone>; run_reviews.sh runs/ 4 opus app;
#      run_judges.sh runs/ sel.json <app clone> 4 opus app; score.py … --origin classification.json
```
Keep `runs/` and `sel.json` outside this repo: they contain colleagues' review comments.
