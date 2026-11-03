# Detail and examples for monnett-app reviews

`pr/REVIEW.md` (the repository's `REVIEW.md` from the PR's target branch) is the standard: scope,
severity, evidence, the "Always check" list, "Do not report", volume and output. This file adds only
what it does not spell out: detail filed under its "Always check" numbers, and real findings the team
accepted (shortened quotes, none from PRs in the backtest sample). Nothing here overrides
`REVIEW.md`; where the two disagree, `REVIEW.md` wins and this file is stale.

Source: 255 substantive review comments on PRs #308–#507 (2026-06-13…10-12) and the 2026-10 month
review (PRs #508–#600). About 56% of those comments were pasted ad-hoc Claude reviews. Trimmed to this
form on 2026-11-03, after `REVIEW.md` reached `develop`.

## 1. Tests can fail
- Assertions on presence but not on order; a test named for a case it never reaches.
- Client tests pin the query and body on the wire (Dio adapter pattern), not a fake repository.
- Do not ask for tests that restate the implementation; propose deleting redundant ones.
> "replacing … with a hardcoded `DiscoverScope.city` … leaves all 4 tests in the file green" (PR416)
> "Removing the `updateSeenPosts` call … leaves all 931 tests green" (PR416)

## 2. Every entry point of the changed behaviour
- Defaults flipped by removing an explicit argument (`needsNavBarHandle`, `useSafeArea`); a feature
  that loses its entry point; a fallback path the PR removed.
- Shared widgets with many callers: `TextWithMention`, `PrimaryAppBar`, `AppBottomSheet`.
- Edge values: DST days (`subtract(Duration(days: 1))` is not "yesterday"), rounding at bucket
  boundaries (`999999` → `1000.0k`), `-1` meaning "unlimited", empty lists from the backend,
  non-ASCII input (de/fr hashtags), EXIF orientation, a null actor (deleted user).
- A `switch` over an enum stays exhaustive; an unknown wire value never maps to a privileged value
  (e.g. `system`).
> "a reaction on your comment now says 'liked your post'" (PR319)
> "on 31 March a post from yesterday is not labelled 'Yesterday'" (PR365)

## 3. Cubit state
- `props` that include fields `fromJson` does not restore make every resume emit. Equatable drops an
  equal state, so a listener never fires twice: a repeated error needs `clearError()`.
- Mutable data outside the emitted state (controller lists on the cubit) does not trigger rebuilds.
- One source of truth: no mirror of cubit state in SharedPreferences or a second cubit.
- An in-flight request or GPS read must not write into the next account's session (generation counter).
- A widget that calls a repository through `getIt` when a cubit for that action exists.
> "A read that resolves after `clearState()` … The next account then opens Discover anchored on
> the previous user's city." (PR425)

## 4. Async races and lifecycle
- Fix for overlapping requests: a request id or generation counter, or serialised writes.
- Capture `GoRouter.of(context)` before a pop (CLAUDE.md "Drawer/overlay navigation").
- Work in `build()` or `initState()` that needs inherited widgets (`precacheImage`) goes to a
  post-frame callback; a post-frame callback scheduled from `build` must not loop on failure.
> "`precacheImage` in `initState` throws in debug … Selecting 5 or more items … red-screens the
> subtree" (PR320)

## 5. Backend contract and deploy order
- The service layer isolates per-item parse failures, so one bad row does not blank a page. No `?? ''`
  for ids.
- Client limits (size, duration, timeouts) match the backend and the tier matrix; take them from
  `/tiers` instead of copying backend values into constants.
> "This hard-casts `commented_by.id` … but on monnett-core `develop` today `CommentAuthorV1Dto`
> only has `public_id/username/name/avatar_url/badge`" (PR467)

## 7. AC and scope
> "flips `legacyDiscoverEnabled` from `true` to `false`. That isn't in the description … Looks like
> a local-testing leftover." (PR420)

## 8. UI, text scale and accessibility
- A `Column` that can exceed a short screen scrolls; one-word labels in fixed boxes clip.
- Do not double-count insets (`viewInsets` inside a `Scaffold` that already resizes).
- Decorative images use `excludeFromSemantics`.
- "Never change what the user is already looking at" (CLAUDE.md "Error Handling"): no
  render-then-correct, such as a fallback palette followed by the real colours.
> "Monthly measures 153pt at 2× system text scale in a 147pt segment" (PR458)
> "`excludeSemantics: true` drops the descendant `GestureDetector`'s semantic tap action" (PR491)

## 9. Navigation
- `go` to a top-level route unmounts the shell and wipes the tab stacks; deeplinks resolve to `push`
  unless the stack must be rebuilt.
- Route observers, analytics included, are attached where the pushes happen (shell vs root navigator).
- Redirects during `AuthInitial`/`AuthLoading`; route constants from `AppRoutes`, not string literals.
> "`state.extra!` has no fallback … the ErrorWidget replaces the whole Navigator" (PR423)

## 10. Error handling
- Side work (cache writes) runs inside `safeCall` or best-effort with a log. A failed save with no UI
  signal is a silent failure.
> "this catch drops the exception without logging it" (PR489)

## 11. Localization the CI guard cannot see
- Adding `@metadata` to a key with two or more placeholders reorders the generated method's
  parameters; call sites must follow (it compiles and renders swapped values).
- Copy fits every call site of a shared widget; the key namespace matches the widget (`common*`).
- A new `// ignore: monnett_lints/hardcoded_string` only for symbols or format tokens, with a reason.
> "Adding metadata here flips the parameter order for two keys, and the call sites were not updated"
> (PR332)

## Not numbered in REVIEW.md
- **Layering and reuse.** New files go to the target tree (`core/ data/ domain/ presentation/`), never
  to legacy dirs. `domain/` never imports `presentation/`; new `core/` files gain no `presentation/`
  or legacy imports; business decisions live in repositories, not in API clients. Reuse the existing
  widget, helper or predicate (`LoadFailure`, `Location.hasUsableLocation`, existing validators).
  > "First domain→presentation import in the codebase" (PR320)
- **Platform specifics.** Android "Approximate" and "Ask every time" location states, iOS `limited`,
  the Settings round-trip; services off vs permission denied. Camera: min zoom < 1 on Android,
  fixed-focus front cameras, orientation lock per platform, an iOS audio session taken by a
  metadata-only `VideoPlayerController`, iCloud files. Autofill: `AutofillGroup` plus
  `finishAutofillContext()`.
  > "Android never had the bug, and there the lock makes the preview and captures come out
  > upside-down" (PR484)
- **Performance.** Synchronous file IO (`lengthSync`, `existsSync`, `readAsBytesSync`) on the UI
  isolate for user media; full-resolution decodes without a size bound (`cacheWidth`); one isolate
  per item; work repeated per rebuild (token lists, `TextPainter` layouts, regex compiles);
  `shrinkWrap` on paginated lists.
- **Theming.** New code uses `AppColors.*`, `AppThemeConsts.fontSize*`/`spacing*` and theme text
  styles; raw colours, hex or magic sizes on added lines are one grouped `nit` per PR.
- **Analytics and privacy.** Events still fire after route moves and keep their meaning under the
  same name; no EXIF or GPS in uploaded media; no client-side badges from untrusted Matrix state.
