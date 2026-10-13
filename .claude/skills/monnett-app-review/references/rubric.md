# Review rubric for monnett-app

Derived from 255 substantive top-level review comments on PRs #308–#507 (2026-06-13…10-12),
ranked by how often the team raises them ("share" = share of comments that raise the topic; one
comment can raise several). Each section: what to check, how it fails, real examples (shortened
quotes, none from PRs in the backtest sample).

Note on the source: about 56% of these comments are pasted output of ad-hoc Claude reviews
(structured "Findings / Blocking / Easy win" blocks). The ranking therefore mixes what humans ask
for and what an ad-hoc AI pass finds. Human-written comments rank docs/dead code, correctness,
backend contract, UI and scope highest; AI-pasted ones rank correctness, async races, UI and state
highest.

## 1. Behaviour regressions and edge cases (share 21%)
- A change that silently alters behaviour users already have: a label that now reads wrong in one
  of its callers, a feature that loses its entry point, a default flipped by removing an explicit
  argument (`needsNavBarHandle`, `useSafeArea`), a fallback path that the PR removed.
- Trace every caller of a changed function, enum mapping or widget parameter, not only the one the
  ticket names. Shared widgets (`TextWithMention`, `PrimaryAppBar`, `AppBottomSheet`) have many.
- Edge values: DST days (`subtract(Duration(days: 1))` is not "yesterday"), rounding at bucket
  boundaries (`999999` → `1000.0k`), `-1` meaning "unlimited", empty lists from the backend,
  non-ASCII input (de/fr hashtags), EXIF orientation, null actor (deleted user).
- `switch` over an enum stays exhaustive; a `default` that hides new values is a finding; an
  unknown wire value must not map to a privileged value (e.g. `system`).
> "a reaction on your comment now says 'liked your post'" (PR319)
> "on 31 March a post from yesterday is not labelled 'Yesterday'" (PR365)

## 2. Comments, dead code and PR description stay true (share 21%)
- Doc comments and inline comments next to changed code still describe it (stale "backstop",
  "park at /splash", "100 MB"). Comments ≤ 5 lines, say why (CLAUDE.md "Comments").
- Code made unreachable or unused by the PR is deleted: parameters nobody passes, branches that
  cannot fire, getters with no caller, ARB keys with no Dart reference, test fakes for dead paths.
- PR description matches the diff: every behaviour change listed (size limits, durations, flag
  flips, removed caches), no claims the code does not make ("magic-byte sniff", "analyze clean").
> "the diff also changes the media size limit … adds a 5 minute upload timeout … Can you add these
> to the description so QA knows to test them?" (PR319)

## 3. Tests prove behaviour (share 19%)
- Would the test fail if the production change were reverted? Reviewers run mutations by hand:
  "breaking the behaviour each test names and checking that it goes red".
- Seeds that equal the default state make the test pass on broken code (seeding `CITY` when
  `CITY` is already the cubit default). Assertions on presence but not order. A test named for a
  case it never reaches.
- Branch-heavy rules (outcome × caller tables, badge permutations, tier gates) get a table test
  (CLAUDE.md "Testing").
- Contract boundaries: every new DTO has a `fromJson` test on a real payload; client tests pin the
  query/body on the wire (Dio adapter pattern), not a fake repository.
- Time-zone logic runs under a fixed non-UTC `TZ`; CI runs in UTC.
- Do not ask for tests that restate the implementation; propose deleting redundant ones.
> "replacing … with a hardcoded `DiscoverScope.city` … leaves all 4 tests in the file green" (PR416)
> "Removing the `updateSeenPosts` call … leaves all 931 tests green" (PR416)

## 4. BLoC/Cubit state handling (share 16%)
- Snapshot-then-await-then-emit: a list read before `await` and emitted after it clobbers changes
  made meanwhile (remove, reset). Re-read `state` after the await or check identity.
- `emit` after `close()` throws: guard with `isClosed` where async work can outlive the cubit.
- `copyWith` that resets a field not passed (`status` → `initial`), `props` including fields that
  `fromJson` does not restore (every resume emits), Equatable dropping an equal state so a
  listener never fires twice (repeat errors need `clearError()`).
- Mutable data outside the emitted state (controller lists on the cubit) does not trigger rebuilds.
- One source of truth: do not mirror cubit state into SharedPreferences or a second cubit.
- Session scope: singleton/hydrated cubits are reset in `clearUserScopedState()`; an in-flight
  request or GPS read must not write into the next account's session (generation counter).
- BLoC/Cubit only for new code; a widget calling a repository via `getIt` when a cubit for that
  action exists is a finding.
> "A read that resolves after `clearState()` … The next account then opens Discover anchored on
> the previous user's city." (PR425)

## 5. UI behaviour, layout and accessibility (share 16%)
- Text scale: `TextPainter` measurements pass `textScaler`; no fixed heights around text
  (CLAUDE.md); a `Column` that can exceed short screens scrolls; one-word labels in fixed boxes clip.
- Full-bleed screens (story viewer, camera, composer) keep media under the status bar; only
  controls go in `SafeArea`. Do not double-count insets (`viewInsets` inside a `Scaffold` that
  already resizes).
- Accessibility: tap targets ≥ 44pt/48dp; icon-only buttons have a semantic label; decorative
  images `excludeFromSemantics`; `excludeSemantics: true` must not drop the tap action.
- "Never change what the user is already looking at" (CLAUDE.md "Error Handling"): no
  render-then-correct (fallback palette, then real colours), errors show message + retry.
- Design fidelity questions (Figma vs AC) are `question`, not `should`.
> "Monthly measures 153pt at 2× system text scale in a 147pt segment" (PR458)
> "`excludeSemantics: true` drops the descendant `GestureDetector`'s semantic tap action" (PR491)

## 6. Async races, re-entrancy and widget lifecycle (share 13%)
- Overlapping requests: a late response for an old query/handle/page overwrites the newer one
  (typeahead, like/unlike, pagination after reload). Fix: request id / generation counter, or
  serialise writes.
- Re-entrancy: double tap on submit, stacked bottom sheets, repeated listener events; gate on an
  in-flight flag or the cubit status.
- `BuildContext` used after `await` without `mounted` / `context.mounted`; navigation after a pop
  (capture `GoRouter.of(context)` first, CLAUDE.md "Drawer/overlay navigation").
- Work in `build()` or `initState()` that needs inherited widgets (`precacheImage`) goes to a
  post-frame callback; post-frame callbacks scheduled from `build` must not loop on failure.
- Timers and stream subscriptions are cancelled on the paths that make them obsolete.
> "`precacheImage` in `initState` throws in debug … Selecting 5 or more items … red-screens the
> subtree" (PR320)

## 7. Backend contract and DTOs (share 11%)
- DTO parsing is fail-fast (CLAUDE.md "DTO parsing"): hard cast required fields; `as T? ?? x`
  only where the contract says nullable; no `try/catch` to default; no `?? ''` for ids.
- The service layer isolates per-item parse failures so one bad row does not blank a page.
- Field names, envelope keys (`items` vs `data`), id types and enum strings match monnett-core
  `develop`, not the ticket text. Check the core DTO when the PR adds or renames a key.
- Merge/deploy order: a hard cast on a key that only exists on an unmerged core branch breaks dev
  builds; say "hold until core PR X is deployed" (`blocking` if the PR targets `develop`).
- Client limits (size, duration, timeouts) match the backend and the tier matrix; do not copy
  backend values into constants when `/tiers` provides them.
> "This hard-casts `commented_by.id` … but on monnett-core `develop` today `CommentAuthorV1Dto`
> only has `public_id/username/name/avatar_url/badge`" (PR467)

## 8. Jira AC and scope (share 11%)
- Each AC item met, including boundary cases listed in the ticket; deviations need a ticket update.
- Unrelated changes (formatting sweeps, `video_player` changes in a toggle PR) → split.
- Local testing leftovers committed: flipped `FeatureFlags`, `?? false` defaults the description
  says not to commit, debug logging.
> "flips `legacyDiscoverEnabled` from `true` to `false`. That isn't in the description … Looks like
> a local-testing leftover." (PR420)

## 9. Localization (share 9%)
- New user-visible text uses `context.l10n`; no new `// ignore: monnett_lints/hardcoded_string`
  except for symbols/format tokens with a reason (CLAUDE.md "Linting Notes").
- Keys present in `intl_en/de/fr.arb`; `@key` block with `placeholders` (and `description`)
  directly after the key; `int` placeholders in ICU plural (lib/l10n/README.md).
- Adding `@metadata` to a key with ≥ 2 placeholders reorders the generated method's parameters;
  call sites must follow (compiles, renders swapped values).
- Keys removed from en are removed from de/fr; keys left without a Dart reference are dead.
- Copy fits every call site of a shared widget; key namespace matches the widget (`common*`).
> "Adding metadata here flips the parameter order for two keys, and the call sites were not updated"
> (PR332)

## 10. Platform specifics, iOS and Android (share 8%)
- Permissions: Android "Approximate" and "Ask every time" states, iOS `limited`, Settings
  round-trip re-arm; services-off vs permission-denied.
- Camera/media: min zoom < 1 on Android, fixed-focus front cameras, orientation lock per
  platform, iOS audio session taken by a metadata-only `VideoPlayerController`, iCloud files.
- Build toolchain: the CI/Codemagic Flutter version (3.41.9) decides which plugin versions resolve;
  a fix that needs a newer plugin may not ship on Android.
- Autofill: `AutofillGroup` + `finishAutofillContext()` for save/update prompts.
> "Android never had the bug, and there the lock makes the preview and captures come out
> upside-down" (PR484)

## 11. Navigation and deeplinks (share 7%)
- `go` vs `push`: `go` to a top-level route unmounts the shell and wipes tab stacks; deeplinks
  resolve to `push` unless the stack must be rebuilt.
- Route observers attached where the pushes happen (shell vs root navigator), including analytics.
- `state.extra` is lost on re-parse (no `extraCodec`): builders must tolerate `null`.
- Redirects during `AuthInitial/AuthLoading`; self-profile and other guards on every entry point,
  not only the helper; push and in-app taps for the same notification land on the same screen.
- Route constants from `AppRoutes`, not string literals.
> "`state.extra!` has no fallback … the ErrorWidget replaces the whole Navigator" (PR423)

## 12. Layering and reuse (share 7% each)
- New files go to the target tree (`core/ data/ domain/ presentation/`), never to legacy dirs.
- `domain/` never imports `presentation/`; `core/` does not gain new imports of `presentation/` or
  legacy dirs in new files; business decisions live in repositories, not in API clients.
- Reuse the existing widget/helper/predicate (`LoadFailure`, `Location.hasUsableLocation`,
  `_resolvedUrl`, existing validators) instead of a second copy that can drift.
> "First domain→presentation import in the codebase" (PR320)

## 13. Error handling (share 7%)
- Repository and service methods return `Result<T, Failure>`; side work (cache writes) inside
  `safeCall` or best-effort with a log.
- No silent failures: an empty `catch`, a `debugPrint` in release, a failed save with no UI signal.
- Hard failures show an error with a retry, not an empty card.
> "this catch drops the exception without logging it" (PR489)

## 14. Performance (share 7%)
- Synchronous file IO (`lengthSync`, `existsSync`, `readAsBytesSync`) on the UI isolate for user
  media; full-resolution decodes without a size bound; one isolate per item.
- Work repeated per rebuild (token lists, `TextPainter` layouts, regex compiles) → cache keyed on
  inputs; `ValueListenableBuilder` on high-frequency values; `shrinkWrap` on paginated lists.
- Large assets decoded at full size for small widgets (`cacheWidth`).

## 15. Theming and design tokens (share 6%)
- New code uses `AppColors.*`, `AppThemeConsts.fontSize*`/`spacing*`, theme text styles; no raw
  `Colors.*`, hex, or magic sizes; no new `ThemeColors` (deprecated).
- Flag as one grouped `nit` per PR, only on added lines.

## 16. Smaller categories (share ≤ 4% each)
- Leaks: controllers, video controllers, subscriptions, temp files not disposed or deleted.
- Feature flags: unreleased work resolves to `kDebugMode`, never `AppEnv`; Remote Config gates for
  subscription UI (CLAUDE.md "Feature flags", "Subscriptions").
- Dependencies: `any` or unbounded ranges when code relies on version-specific behaviour.
- Logging: `print`/`developer.log` in hot paths; silent `catch` without a log.
- Analytics: events still fire after route moves; event semantics unchanged under the same name.
- Privacy: EXIF/GPS in uploaded media; client-side badges from untrusted Matrix state.
- Matrix/chat: send failure states, queue retries (rare in this sample).


## Do not report
- Legacy patterns in untouched code (see known-drift.md): raw colours and `ThemeColors` elsewhere,
  long comments elsewhere, `getIt` in widgets without an existing cubit, `data/` importing
  `domain/` entities, imports from `lib/common` in existing files, Riverpod remnants.
- Formatting, import order, `directives_ordering`, analyzer infos that CI does not gate, unless the
  info hides a real defect (`use_build_context_synchronously`).
- Requests to add a mocking library or a test framework not in the repo (no mocktail, mockito or
  bloc_test; fakes are hand-written).
- `dart format` reflows in untouched files as a finding (mention once in `notes` if large).
- Product or design choices that match Figma but not the ticket wording: `question` at most.
- Speculative "could be slow" without a concrete hot path or measured size.
- Asking for a lock file change: `pubspec.lock` is gitignored.
- ARB housekeeping the pre-commit hook owns: a missing `description`, `@key` blocks on keys without
  placeholders, blank lines or escapes in ARB files. Only placeholder metadata matters (section 9).
- A `TODO` without a ticket reference.
