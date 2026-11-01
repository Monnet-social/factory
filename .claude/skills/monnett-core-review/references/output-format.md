# Output format

Write one JSON object to the output file (default `review.json` in the workspace):

```json
{
  "pr": 370,
  "summary": "One to three lines: overall assessment and the most important problem.",
  "verdict": "changes_requested",
  "findings": [
    {
      "id": "F1",
      "severity": "blocking",
      "category": "flyway",
      "path": "src/main/resources/db/migration/V20261029_0915__user_display_name_trigram_index.sql",
      "line": 13,
      "title": "Trigram index cannot serve the LOWER(...) LIKE predicate",
      "body": "The query filters on `LOWER(display_name) LIKE :p`, the index is on `display_name gin_trgm_ops`. Postgres uses an expression index only for the same expression, so the search stays a sequential scan.",
      "fix": "Index `LOWER(display_name) gin_trgm_ops`, or drop LOWER and use ILIKE.",
      "confidence": "high",
      "source": "llm",
      "prior_id": null
    }
  ],
  "fixed_prior": [
    {"prior_id": 879004468, "evidence": "Tests for the legacy no-issuer token and both wrong-issuer cases added in `JwtServiceAdminSigningModeTest.java:590-640`."}
  ],
  "merge": {
    "confidence": 55,
    "reason": "One line: what a human should look at first.",
    "flag_gated": false,
    "flag_evidence": ""
  },
  "dropped_prechecks": [
    {"check": "bruno-sync", "path": "…", "reason": "endpoint is a webhook, not called by clients"}
  ],
  "notes": "Optional: missing inputs, parts not reviewed (e.g. generated files), assumptions."
}
```

| Field | Values |
|---|---|
| `verdict` | `approve` (no findings), `approve_with_nits` (only nit/question), `changes_requested` (any blocking/should) |
| `severity` | `blocking`, `should`, `nit`, `question` |
| `category` | `tests`, `ac-scope`, `correctness`, `privacy-security`, `flyway`, `api-contract`, `layering`, `transactions`, `i18n`, `performance`, `docs-deadcode`, `side-files`, `logging`, `reuse`, `events`, `memory`, `concurrency`, `deploy-compat`, `analytics-disclosure` |
| `line` | line number in the PR head version of the file; `null` for file-level findings |
| `confidence` | `high` or `medium` |
| `source` | `llm` or `precheck:<check-id>` (kept pre-check findings, wording may be improved) |
| `fixed_prior` | earlier `kind: finding` items from `pr/prior-review.json` that the PR head fixes completely, each with one line of evidence; their threads are resolved. Empty on a first review |
| `merge` | `confidence` 0–100: your estimate that a human finds no blocking or should-fix problem this review missed (the poster caps it); `reason` one line; `flag_gated` and `flag_evidence` (`path:line`) only for a `ui-candidate` PR in `pr/risk.json` |
| `accepted` | optional, on a kept `blocking` finding with `prior_id`: `true` when another human reviewer accepted the author's reply |
| `prior_id` | `id` of the earlier finding, summary or human comment (`kind: human`) in `pr/prior-review.json` that raised the same problem, else `null`. Findings with a `prior_id` are never posted again |

`body` ≤ 6 lines, Markdown allowed, no headings. Findings ordered by severity, then file.
