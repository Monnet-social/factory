# Output format

Write one JSON object to the output file (default `review.json` in the workspace):

```json
{
  "pr": 467,
  "summary": "One to three lines: overall assessment and the most important problem.",
  "verdict": "changes_requested",
  "findings": [
    {
      "id": "F1",
      "severity": "blocking",
      "category": "api-contract",
      "path": "lib/data/data_sources/dtos/comment_dto.dart",
      "line": 42,
      "title": "Hard cast on `commented_by.id`, which core develop does not send",
      "body": "`CommentAuthorV1Dto` on core `develop` has `public_id`, `username`, `name`, `avatar_url`, `badge`; there is no `id`. `json['id'] as String` throws on every comment, so the thread screen shows the error state on dev builds.",
      "fix": "Read `public_id`, or hold the PR until the core PR that adds `id` is deployed.",
      "confidence": "high",
      "source": "llm",
      "prior_id": null
    }
  ],
  "fixed_prior": [
    {"prior_id": 879004468, "evidence": "`if (!context.mounted) return;` added after the await in `lib/presentation/screens/settings/settings_screen.dart:118`."}
  ],
  "dropped_prechecks": [
    {"check": "context-after-await", "path": "…", "reason": "the caller checks mounted before this callback runs"}
  ],
  "notes": "Optional: missing inputs, parts not reviewed, assumptions."
}
```

| Field | Values |
|---|---|
| `verdict` | `approve` (no findings), `approve_with_nits` (only nit/question), `changes_requested` (any blocking/should) |
| `severity` | `blocking`, `should`, `nit`, `question` |
| `category` | `correctness`, `tests`, `docs-deadcode`, `state`, `ui-a11y`, `async-lifecycle`, `api-contract`, `ac-scope`, `l10n`, `platform`, `navigation`, `layering`, `duplication`, `error-handling`, `performance`, `theming`, `leaks`, `flags-config`, `deps`, `logging`, `analytics`, `privacy-security` |
| `path` | path relative to the app repository root (e.g. `lib/…`, `test/…`), no `app/` prefix |
| `line` | line number in the PR head version of the file; `null` for file-level findings |
| `confidence` | `high` or `medium` |
| `source` | `llm` or `precheck:<check-id>` (kept pre-check findings, wording may be improved) |
| `fixed_prior` | earlier `kind: finding` items from `pr/prior-review.json` that the PR head fixes completely, each with one line of evidence; their threads are resolved. Empty on a first review |
| `prior_id` | `id` of the earlier finding or summary in `pr/prior-review.json` that raised the same problem, else `null`. Findings with a `prior_id` are never posted again |

`body` ≤ 6 lines, Markdown allowed, no headings. Findings ordered by severity, then file.
