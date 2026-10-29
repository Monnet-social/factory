# Review rubric for monnett-core

Derived from 400 human review comments on PRs #188–#387 (2026-05…10), ranked by how often the team
raises them, and updated after the 2026-10 month review (PRs #388–#482). Each section: what to check,
how it fails, real examples (shortened quotes). `REVIEW.md` in the repository is the team's standard;
where this file disagrees with it, `REVIEW.md` wins.

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
- No `@Transactional` on test methods outside repository-only tests (CLAUDE.md "Testing"); DB tests
  call `cleaner.reset()` in `@BeforeEach`; fixtures via `TestEntityUtils`.
- Tests do not depend on the order in which other tests run (shared rows, static state, clocks).
- A finding about an internal mock names the existing double to use (`testdoubles/`, see known-drift).
> "asserts `hasSize(2)` but never checks which usernames come back … Removing the `ORDER BY` would leave it green." (PR293)
> "`allSatisfy` passes on an empty list." (PR318)

## 2. Jira AC and scope
- Each AC item implemented and tested; behaviour the AC forbids is a `blocking` finding.
- Deliberate deviation from the AC → ask to update the ticket (`question`). A decision or follow-up
  ticket named in the description, ticket or a code comment is not a defect.
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
- Existing data: rows already in production, jobs queued with the old payload, users who never had
  the new field. Before reporting a missing guard, look for it: upstream dedupe
  (`existsByWebhookEventId`), `CHECK`/unique constraints, routing in other handlers, tests elsewhere.
- Claims about RevenueCat, Brevo, Synapse, FCM or Gcore behaviour cite the vendor docs or existing
  code; without a source, ask a `question`.
> "`switch` here is a statement, not an expression, so the compiler won't flag a new `OnboardingSurface`." (PR336)

## 4. Privacy: block and visibility rules on every read path
- Any new or changed query that returns users, posts, comments, mentions, reposts, story viewers,
  typeahead/suggestions must apply the block filter (both directions) and visibility (`ShareWith`,
  close friends, private accounts, mature content).
- No profile data of a blocked user leaks through nested objects (repost author, mentioned users).
- Entitlement/tier gates cannot be bypassed via another endpoint or v0 path.
- User input in `LIKE` must escape `%` and `_`. No internal numeric ids on the wire of new endpoints.
- Analytics figures (MNT-942 and later): small-cell suppression survives subtraction across rows, days
  and totals; a zero is not a small cell unless the conventions say so; the database stays
  authoritative for a GDPR objection (no cache can undo it). A broken objection or a recoverable
  blanked cell is `blocking`.
> "A blanked bar can be recovered through WAU on the same Sunday." (PR453)
> "Feed mentions bypass the block filter (please fix before merge)." (PR334)

## 5. Flyway, schema and rolling deploy
- Names follow CLAUDE.md "Flyway migrations" (MNT-1034): `V<YYYYMMDD>_<HHmm>__name.sql` in UTC,
  out-of-order on. A new `V00NN__` file is `blocking` (the legacy block V0001–V0051 is frozen; a
  branch cut before MNT-1034 does not run `MigrationHygieneTest`). Never report next-number, gap or
  "an open PR uses the same number": that scheme is gone.
- Never edit or revert a migration that may be applied anywhere (checksum failure at boot). All DDL
  on `v1.` qualified; `IF [NOT] EXISTS`; data statements re-runnable (`ON CONFLICT DO NOTHING`, a
  `WHERE` that excludes rows already updated).
- `UPDATE`/`DELETE` without `WHERE` in one transaction over big tables; data backfills batched.
- Index matches the query expression (`LOWER(x)`, trigram `gin_trgm_ops` for `LIKE '%…%'`, column
  order for the `WHERE`/`ORDER BY`); plain `CREATE INDEX` on big tables locks writes (precedent for
  `CONCURRENTLY`: V0009, V0014).
- Entity mapping matches the migration (types, nullability, enum values incl. Postgres enum types).
- Rolling deploy: old pods keep running during a deploy. A dropped or renamed column or table that
  old code still reads is `blocking` unless the PR is the second step of a two-step change. New
  config or secrets are listed in the description with the deploy order.
> "This index can't be used by the query it was added for." (PR370)

## 6. v1 API contract
- Business failures: HTTP 200 + `SuCoMe.fail(DomainResultCode.X, …)`; not 4xx/5xx, not exceptions.
  4xx only for protocol/validation errors handled by the framework, 429 for rate limits, webhooks.
- Typed response record per endpoint; JSON field casing matches the sibling DTOs of the same
  resource and never mixes inside one object (do not enforce snake_case or camelCase otherwise); list
  payloads named by content (`posts`, `users`) + `meta.pagination`, no `data`/`items`, no `message` in
  the payload. Pagination has a unique tie-breaker; `@Schema` describes the wrapped response.
- Paths: plural collections, singular singletons/namespaces/toggles; prefix `/v1/app` or `/v1/admin`.
- `@Operation`/`@APIResponse` accurate; security annotation on every endpoint.
- Changing a wire shape the app already uses (renamed/removed field, changed type, a result code's
  meaning, HTTP 200 → 400 for a code the app reads) → `blocking` unless the PR states the app
  coordination.
> "business outcomes should come back as HTTP 200 with the `SuCoMe` envelope … not mapped to 4xx/5xx." (PR298)

## 7. Layering, reuse and boundaries (CLAUDE.md tenets, `core-clean-code` § 1–2, `core-architecture` § 1, § 3)
- Resources: HTTP only, call services, never repositories/entities (tenet 1).
- Services: business logic, return `SuCoMe`, map entities → DTOs via `data/mappers` (tenet 9).
- No entities inside DTOs; one file per public type (tenet 6); EnrichmentContext via the factory (tenet 4).
- Async side effects via `EventPublisher` domain events; `@ObservesAsync` listeners catch and log (tenet 5).
  A new injection of `Event<T>` is `should` (`core-architecture` § 3).
- One definition per rule: visibility, block, account state, status sets, deep-link and URL building,
  key prefixes. Call the existing definition (e.g. `UserListQuery.visibleTo`, `NotificationService`,
  `EntitlementService`) and name it; never a second predicate or mapper.
- A new write to another feature's tables, or a managed entity crossing a feature boundary, needs a
  stated reason (`core-architecture` § 1); a read is allowed.
- Enum values carry one meaning; add `NONE`/`UNSPECIFIED` rather than overloading (tenet 8).
> "carries a managed `PostEntity`, which goes against tenet 9 in CLAUDE.md (repositories return entities, services return DTOs)." (PR314)

## 8. Transactions, concurrency and async context (`core-clean-code` § 6)
- `@Transactional(REQUIRES_NEW)` inside a flow breaks atomicity of the caller.
- Load-modify-flush on entities without `@Version`/`@DynamicUpdate` loses concurrent updates of other
  columns (e.g. `UserAccountEntity`) → targeted `UPDATE … SET col = … WHERE …`.
- Check-then-insert races → unique constraint + idempotent insert; locks never held across network I/O
  (Synapse, RevenueCat, email, storage).
- Batch/sweep jobs isolate failures per item; `@ObservesAsync` + `@Transactional` commit errors escape
  the inner catch.
- A raceable write uses a constraint plus `IdempotentInsert` or an atomic update and has a
  `ConcurrentRequests` test.
- Code on `@ObservesAsync` or JobRunr threads opens its own transaction before it reads the database.
  No remote call inside `@Transactional`. A `@Scheduled` method that enqueues work is safe when every
  pod runs it. Metrics and events fire after commit.
> "`@Transactional(REQUIRES_NEW)` suspends the caller's transaction … That breaks atomicity." (PR290)

## 9. i18n
- Every user-facing message is a `MessageLocalizer` key (`I18nKeys`); app-facing keys exist in EN, FR
  and DE (`MessageLocalizerTest` fails otherwise, so do not report parity). `admin.*` keys are English
  only (MNT-900): never ask for FR or DE texts for them.
- The interpolator is custom: MessageFormat syntax (`''`, `{0,number,#}`) renders literally.
- Never put `e.getMessage()` or exception text into a client message (leaks internals, untranslated).
- Locale is not available inside `@ObservesAsync` handlers; resolve it from the user, not the request.

## 10. Performance
- N+1: repository calls inside loops/streams; lazy associations used in mappers without `JOIN FETCH`.
- Re-querying an entity or row already loaded in the same flow (count the reads per request);
  unbounded queries without limit; limits that differ between two paths returning the same data.
- Info-level logs on hot paths (feed, per-item loops).
- Memory and query count bounded by the page size (`core-architecture` § 5): no `IN (:ids)` list that
  grows with the social graph, every cache bounded.

## 11. Docs and dead code
- Javadoc/comments changed code still describe it correctly; comments ≤ 5 lines, say *why*, do not
  narrate history.
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
- A new analytics reading assumption → `docs/analytics/figure-conventions.md`.

## Do not report
- PRs into `main` (release PRs of reviewed work; `bb.py prepare` skips them).
- Legacy patterns in untouched code (see known-drift.md): long Javadocs elsewhere, v0 design,
  field injection, `@RolesAllowed("USER")` string literals.
- `ResponseUtil` and the shared v0 user row (`MinimalUserDTO`) used from v1 code.
- `core-clean-code` § 3–5 style (loops vs streams, utility classes, method length, `Linked*`), unless
  the diff makes the code clearly harder to read. Formatting, import order, line length, `var` usage.
- What the tests already fail on: migration names (`MigrationHygieneTest`), message key parity
  (`MessageLocalizerTest`), `@AnalyticsUse` marks (`AnalyticsUseMarkingTest`), config inventory
  (`AppConfigTest`).
- French or German texts for `admin.*` keys; DTO casing beyond "match the siblings".
- Proactive v0 refactors — CLAUDE.md forbids them unless the ticket needs it.
- Speculative "could be slow" without a concrete N+1 or unbounded query. A literal used once.
- A gap that heals itself on the next scheduled run where a test would need timing waits (`question`
  at most).
