# Review rubric for monnett-core

Derived from 400 human review comments on PRs #188–#387 (2026-05…10), ranked by how often the team
raises them. Each section: what to check, how it fails, real examples (shortened quotes).

## 1. Tests prove the behaviour (most frequent)
Check every new/changed test:
- Would it fail if the production change were reverted? Assertions only on size, status or
  "not null" usually pass on broken code. `assertThat(list).allSatisfy(...)` passes on an empty list.
- Order, pagination (`next_cursor`, page boundaries) and `total` are asserted when the feature sorts
  or pages. The returned items are identified (ids/usernames), not only counted.
- One test per AC item and per failure branch (not found, blocked, forbidden, limit reached, invalid input).
- Async: no `await` that does not actually wait for the effect; listener tests call the listener directly.
- Altitude: prefer resource-level `@QuarkusTest` + RestAssured through the HTTP contract; service-level
  only for large permutation matrices or unreachable paths.
- No mocks of internal beans (repositories, services, `EventPublisher`, `SubscriptionGate`, …).
  Allowed: `AsyncJobScheduler`, external services via `testdoubles/`.
- RestAssured tests never `@Transactional`; DB tests call `cleaner.reset()` in `@BeforeEach`;
  fixtures via `TestEntityUtils`.
> "asserts `hasSize(2)` but never checks which usernames come back … Removing the `ORDER BY` would leave it green." (PR293)
> "`allSatisfy` passes on an empty list." (PR318)

## 2. Jira AC and scope
- Each AC item implemented and tested; behaviour the AC forbids is a `blocking` finding.
- Deliberate deviation from the AC → ask to update the ticket (`question`).
- Changes unrelated to the ticket (drive-by refactors, v0 refactors not required by the ticket,
  unrelated files) → `should`.
> "Blocker — MNT-326 AC violation." (PR327)

## 3. Domain correctness and state machines
- Subscriptions / RevenueCat webhooks / trials / grants: event order, stale or duplicate events,
  idempotency on retries (no repeated notifications or emails), effective tier vs store tier.
- Post lifecycle and media status, story expiry, delete-account flows: every state transition covered,
  no transition from a terminal state.
- `switch` over an enum must be an exhaustive switch *expression* (no `default` hiding a new value).
- Null/empty inputs, boundary values (0, limit, limit+1), time zones (`Instant` vs local dates).
- Idempotency under races: a re-read after a lost insert race must check the row's *state* (e.g.
  status BLOCKED vs ACCEPTED), not only its existence; guards must not run before the idempotent
  short-circuit (a retry must return the same result as the first call).
- Enum ↔ wire/string mappings: duplicate or empty values that make `fromX()` ambiguous; a mapping
  that diverges from the one the app knows; new enum values silently falling into `default`.
- Expiry/grace logic: billing grace periods, past `expires_date` with an active entitlement, transient
  lookup failures treated as "expired"; one bad item must not stall or force-expire a batch.
> "`switch` here is a statement, not an expression, so the compiler won't flag a new `OnboardingSurface`." (PR336)

## 4. Privacy: block and visibility rules on every read path
- Any new or changed query that returns users, posts, comments, mentions, reposts, story viewers,
  typeahead/suggestions must apply the block filter (both directions) and visibility (`ShareWith`,
  close friends, private accounts, mature content).
- No profile data of a blocked user leaks through nested objects (repost author, mentioned users).
- Entitlement/tier gates cannot be bypassed via another endpoint or v0 path.
- User input in `LIKE` must escape `%` and `_`.
> "Feed mentions bypass the block filter (please fix before merge)." (PR334)

## 5. Flyway and schema
- Version unique against the target branch and other open PRs (pre-check does this), next number.
- Never edit an existing migration. All DDL on `v1.` qualified; `IF [NOT] EXISTS`.
- `UPDATE`/`DELETE` without `WHERE` in one transaction over big tables; data backfills batched.
- Index matches the query expression (`LOWER(x)`, trigram `gin_trgm_ops` for `LIKE '%…%'`, column
  order for the `WHERE`/`ORDER BY`); plain `CREATE INDEX` on big tables locks writes.
- Entity mapping matches the migration (types, nullability, enum values incl. Postgres enum types).
> "`develop` already has `V0029__…`, so after merge there are two migrations at version 29 and Flyway aborts." (PR370)
> "This index can't be used by the query it was added for." (PR370)

## 6. v1 API contract
- Business failures: HTTP 200 + `SuCoMe.fail(DomainResultCode.X, …)`; not 4xx/5xx, not exceptions.
  4xx only for protocol/validation errors handled by the framework, 429 for rate limits, webhooks.
- Typed response record per endpoint; JSON fields snake_case via `@JsonProperty`; list payloads named
  by content (`posts`, `users`) + `meta.pagination`, no `data`/`items`, no `message` in the payload.
- Paths: plural collections, singular singletons/namespaces/toggles; prefix `/v1/app` or `/v1/admin`.
- `@Operation`/`@APIResponse` accurate; security annotation on every endpoint.
- Changing a wire shape the app already uses (renamed/removed field, changed type) → `blocking`
  unless the PR states the app coordination.
> "business outcomes should come back as HTTP 200 with the `SuCoMe` envelope … not mapped to 4xx/5xx." (PR298)

## 7. Layering and reuse (CLAUDE.md tenets)
- Resources: HTTP only, call services, never repositories/entities (tenet 1).
- Services: business logic, return `SuCoMe`, map entities → DTOs via `data/mappers` (tenet 9).
- No entities inside DTOs; one file per public type (tenet 6); EnrichmentContext via the factory (tenet 4).
- Async side effects via `EventPublisher` domain events; `@ObservesAsync` listeners catch and log (tenet 5).
- Reuse the existing service (e.g. `NotificationService`, `EntitlementService`) instead of copying its
  logic or SQL predicates; no second mapper for the same conversion.
- Enum values carry one meaning; add `NONE`/`UNSPECIFIED` rather than overloading (tenet 8).
> "carries a managed `PostEntity`, which goes against tenet 9 in CLAUDE.md (repositories return entities, services return DTOs)." (PR314)

## 8. Transactions and concurrency
- `@Transactional(REQUIRES_NEW)` inside a flow breaks atomicity of the caller.
- Load-modify-flush on entities without `@Version`/`@DynamicUpdate` loses concurrent updates of other
  columns (e.g. `UserAccountEntity`) → targeted `UPDATE … SET col = … WHERE …`.
- Check-then-insert races → unique constraint + idempotent insert; locks never held across network I/O
  (Synapse, RevenueCat, email, storage).
- Batch/sweep jobs isolate failures per item; `@ObservesAsync` + `@Transactional` commit errors escape
  the inner catch.
> "`@Transactional(REQUIRES_NEW)` suspends the caller's transaction … That breaks atomicity." (PR290)

## 9. i18n
- Every user-facing message is a `MessageLocalizer` key (`I18nKeys`) present in EN, FR and DE bundles;
  no duplicate keys; no hardcoded user-facing strings.
- The interpolator is custom: MessageFormat syntax (`''`, `{0,number,#}`) renders literally.
- Never put `e.getMessage()` or exception text into a client message (leaks internals, untranslated).
- Locale is not available inside `@ObservesAsync` handlers; resolve it from the user, not the request.

## 10. Performance
- N+1: repository calls inside loops/streams; lazy associations used in mappers without `JOIN FETCH`.
- Re-querying an entity or row already loaded in the same flow (count the reads per request);
  unbounded queries without limit; limits that differ between two paths returning the same data.
- Info-level logs on hot paths (feed, per-item loops).

## 11. Docs and dead code
- Javadoc/comments changed code still describe it correctly; comments ≤ 5 lines, say *why*.
- No unused fields/params/config/imports, no commented-out code, no placeholders/TODO left by the PR.
  A new config property that nothing reads is dead config.
- Duplicated blocks the PR adds (same builder/query in two branches) → extract.
- Test names and `@DisplayName` still describe what the test checks (renames!).
- PR description claims match the diff.

## 11b. Magic values (tenet 7)
- Hardcoded time zones (`Europe/Luxembourg`), limits, durations, Redis key prefixes in new code →
  `AppConfig` or `Constants`.

## 12. Side files move with the change (pre-check covers most)
- New `${ENV_VAR}` → `.env.example` (always) and `.env.local` (if needed locally).
- Endpoint added/changed/removed → Bruno `.bru` in the feature folder.
- Config getters changed → `AppConfigTest.expectedPropertyPaths()`.
- New secret/webhook → deploy order noted in the PR description.

## Do not report
- Legacy patterns in untouched code (see known-drift.md): long Javadocs elsewhere, v0 design,
  `ResponseUtil` from `api.v0`, field injection, `@RolesAllowed("USER")` string literals.
- Formatting, import order, line length, `var` usage.
- Proactive v0 refactors — CLAUDE.md forbids them unless the ticket needs it.
- Speculative "could be slow" without a concrete N+1 or unbounded query.
