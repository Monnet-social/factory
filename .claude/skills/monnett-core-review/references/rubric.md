# Detail and examples for monnett-core reviews

`pr/REVIEW.md` (the repository's `REVIEW.md` from the PR's target branch) is the standard: scope,
severity, evidence, the "Always check" list, "Do not report", volume and output. This file adds only
what it does not spell out: detail filed under its "Always check" numbers, and real findings the team
accepted (shortened quotes). Nothing here overrides `REVIEW.md`; where the two disagree, `REVIEW.md`
wins and this file is stale.

Source: 400 human review comments on PRs #188–#387 (2026-05…10) and the 2026-10 month review
(PRs #388–#482). Trimmed to this form on 2026-11-03, after `REVIEW.md` reached `develop`.

## 1. Tests prove the behaviour
- `assertThat(list).allSatisfy(...)` passes on an empty list. Assertions only on size, status or "not
  null" usually pass on broken code.
- Paging features assert `next_cursor`, page boundaries and `total`, and identify the returned items
  (ids or usernames), not only their count.
- Async: no `await` that does not wait for the effect; listener tests call the listener directly.
- Altitude: resource-level `@QuarkusTest` with RestAssured through the HTTP contract; service level
  only for large permutation matrices or paths HTTP cannot reach.
> "asserts `hasSize(2)` but never checks which usernames come back … Removing the `ORDER BY` would leave it green." (PR293)
> "`allSatisfy` passes on an empty list." (PR318)

## 2. AC and scope
> "Blocker — MNT-326 AC violation." (PR327)

## 3. Domain correctness and existing data
- No transition out of a terminal state: post lifecycle, media status, story expiry, delete-account.
- Null and empty input, boundary values (0, limit, limit+1), `Instant` vs local dates.
- After a lost insert race, the re-read checks the row's *state* (BLOCKED vs ACCEPTED), not only that
  it exists. Guards do not run before the idempotent short-circuit: a retry returns what the first
  call returned.
- Enum to wire or string mappings: duplicate or empty values that make `fromX()` ambiguous; a mapping
  that differs from the one the app knows.
- Expiry and grace logic: billing grace periods, a past `expires_date` with an active entitlement, a
  transient lookup failure treated as "expired". One bad item must not stall or force-expire a batch.
> "`switch` here is a statement, not an expression, so the compiler won't flag a new `OnboardingSurface`." (PR336)

## 4. Privacy
- Read paths that need the filters: users, posts, comments, mentions, reposts, story viewers,
  typeahead and suggestions. Visibility includes `ShareWith`, close friends, private accounts and
  mature content.
> "Feed mentions bypass the block filter (please fix before merge)." (PR334)
> "A blanked bar can be recovered through WAU on the same Sunday." (PR453)

## 5. Concurrency and async context
- `@Transactional(REQUIRES_NEW)` inside a flow suspends the caller's transaction and breaks its
  atomicity.
- Load-modify-flush on an entity without `@Version` or `@DynamicUpdate` (e.g. `UserAccountEntity`)
  loses concurrent updates of other columns. Use a targeted `UPDATE … SET col = … WHERE …`.
- No lock held across network I/O (Synapse, RevenueCat, email, storage).
- Batch and sweep jobs isolate failures per item. With `@ObservesAsync` plus `@Transactional`, a
  commit error escapes the inner `catch`.
> "`@Transactional(REQUIRES_NEW)` suspends the caller's transaction … That breaks atomicity." (PR290)

## 6. One definition per rule
- Existing definitions to name: `UserListQuery.visibleTo`, `NotificationService`, `EntitlementService`.

## 7. Migrations
- Never report "next number", a gap, or "an open PR uses the same number": the numbered scheme is gone
  since MNT-1034.
- `UPDATE` or `DELETE` without `WHERE` over a big table in one transaction; data backfills batched.
- Index shape: `LOWER(x)` when the query lowers, trigram `gin_trgm_ops` for `LIKE '%…%'`, column order
  that matches `WHERE` and `ORDER BY`.
- Entity mapping includes Postgres enum types and their values.
> "This index can't be used by the query it was added for." (PR370)

## 9. v1 contract
- 4xx is only for protocol and validation errors the framework handles, 429 for rate limits, and
  webhooks. A changed wire shape the app reads is Blocking unless the PR states the app coordination.
> "business outcomes should come back as HTTP 200 with the `SuCoMe` envelope … not mapped to 4xx/5xx." (PR298)

## 10. Boundaries, events and memory
- Lazy associations used in mappers without `JOIN FETCH` are an N+1.
- An entity or row loaded twice in one flow (count the reads per request); two paths that return the
  same data with different limits; info-level logs on hot paths (feed, per-item loops).
> "carries a managed `PostEntity`, which goes against tenet 9 in CLAUDE.md (repositories return entities, services return DTOs)." (PR314)

## 13. Comments and description stay true
- Dead code the PR leaves: unused fields, parameters, imports, commented-out code, a config property
  nothing reads. A block the PR adds twice (same builder or query in two branches) is extracted.
- Test names and `@DisplayName` still describe what the test checks after a rename.

## i18n (not a numbered check in REVIEW.md)
- Every user-facing message is a `MessageLocalizer` key (`I18nKeys`).
- The interpolator is custom: MessageFormat syntax (`''`, `{0,number,#}`) renders literally.
- Never put `e.getMessage()` or exception text into a client message: it leaks internals and is not
  translated.
- No request locale inside `@ObservesAsync` handlers; resolve the locale from the user.
