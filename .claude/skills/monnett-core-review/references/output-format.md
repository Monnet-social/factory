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
      "path": "src/main/resources/db/migration/V0029__user_display_name_trigram_index.sql",
      "line": 13,
      "title": "Trigram index cannot serve the LOWER(...) LIKE predicate",
      "body": "The query filters on `LOWER(display_name) LIKE :p`, the index is on `display_name gin_trgm_ops`. Postgres uses an expression index only for the same expression, so the search stays a sequential scan.",
      "fix": "Index `LOWER(display_name) gin_trgm_ops`, or drop LOWER and use ILIKE.",
      "confidence": "high",
      "source": "llm",
      "prior_id": null
    }
  ],
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
| `category` | `tests`, `ac-scope`, `correctness`, `privacy-security`, `flyway`, `api-contract`, `layering`, `transactions`, `i18n`, `performance`, `docs-deadcode`, `side-files`, `logging` |
| `line` | line number in the PR head version of the file; `null` for file-level findings |
| `confidence` | `high` or `medium` |
| `source` | `llm` or `precheck:<check-id>` (kept pre-check findings, wording may be improved) |
| `prior_id` | `id` of the earlier finding in `pr/prior-review.json` that raised the same problem, else `null`. Findings with a `prior_id` are never posted again |

`body` ≤ 6 lines, Markdown allowed, no headings. Findings ordered by severity, then file.
