# Known drift: where CLAUDE.md and the code disagree (monnett-app)

Measured on `develop` at `b5b13145` (2026-10-12), CI and l10n items updated 2026-10-29. Use this to
avoid false findings. `REVIEW.md` in the repository wins where this file disagrees with it.

## CLAUDE.md is stale here: trust the code
- Legacy list names `lib/providers/`: it does not exist. Riverpod survives in 7 files
  (`lib/main.dart`, 3 in `lib/common/widgets/`, `lib/views/home/create_post/permission_screen.dart`,
  2 in `lib/presentation/widgets/components/`). `lib/cubits/` holds 1 file. Real legacy dirs:
  `lib/common/` (22 files, imported by 136), `lib/services/` (10, imported by 52),
  `lib/controllers/` (14, incl. Matrix `outgoing_queue_controller.dart`, imported by 28),
  `lib/views/` (5).
- `lib/legacy/` (legacy Discover, mentioned in older PRs) no longer exists.
- The target tree omits folders that are legitimate and in use: `core/{deeplink, enums, error,
  init, l10n, media, notifications, router, validators}`, `data/mappers/`,
  `presentation/post_viewer/`, `presentation/widgets/{components, sections, templates}`.
  `presentation/widgets/scaffolds/` does not exist. Do not ask to move files into `scaffolds/`.
- `domain/repositories/` holds the concrete implementations, which import `data/` (17 files).
  This is the design, not a violation.
- "`data/` depends only on `core/`": 34 `data/` files import `domain/` entities (return types of
  API clients). Reviewers agreed (PR438) not to enforce it per PR; only business decisions inside
  an API client are a finding.
- `core/` imports `presentation/` in 27 files (navigation helper, notification navigator, init).
  Pre-existing; flag only new imports in new `core/` files.
- `lib/domain/entities/` has `fromJson` in 35 files (entities parsing JSON, e.g. `MediaPost`).
  New DTOs go to `data/data_sources/dtos/*_dto.dart`; do not ask to migrate existing entities.
- "Every DTO ships a `fromJson` test": 11 of 23 DTO files have no test file. Applies to new DTOs.
- CI gates (`bitbucket-pipelines.yml`), so never report them: `hardcoded_string` in widget arguments
  is an ERROR-level lint since MNT-899 and fails `flutter analyze`; `.githooks/l10n/arb_guard.py`
  fails on key parity across every `intl_*.arb`, duplicate keys, ICU syntax, argument parity,
  `'` before `{`, metadata outside the template, a missing `description`, unused keys and the French
  `=0` case; `.ci/naming_checks.sh` fails on branch and commit names (`MNT-n|bugfix|chore|release`).
- Other lint rules in "Linting Notes" are not gated (`--no-fatal-warnings --no-fatal-infos`):
  `avoid_print`, `use_build_context_synchronously`, `directives_ordering`, `always_specify_types`.
- Four locales: `en` (template), `de`, `fr`, `lb` (MNT-1061). `localizationEnabled` is gone (MNT-704).
- Time zones in CI: `test/core/util` runs under `TZ=Europe/Luxembourg`, `notification_dto_test.dart`
  under `TZ=Pacific/Honolulu`; every other test runs in UTC.
- Toolchain: CI image and Codemagic build with Flutter 3.41.9; CLAUDE.md and some code comments
  reason about 3.47 behaviour. A plugin fix that needs a newer Flutter may resolve to an older
  version in CI (PR490 camerax).
- `pubspec.lock` is gitignored. Dependency versions resolve at build time; there is no lock diff
  to review.
- No code generation (no build_runner, freezed, json_serializable). DTOs and mappers are
  hand-written; the only generated files are the gitignored l10n classes.

## Legitimate exceptions to rules
- `// ignore: monnett_lints/hardcoded_string` with a reason for numbers and symbols (6 existing
  sites). Another escape hatch, `// l10n-ignore-next-line`, is accepted by the lint (0 uses).
- `Navigator.push(MaterialPageRoute(...))` for value-returning modal editors (crop editor, 4 files)
  instead of `AppRoutes`.
- `AppEnv.isProd` in Remote Config defaults and RevenueCat config (`config.dart`, `firebase.dart`,
  `revenuecat_config.dart`, `app_config_state.dart`): documented in CLAUDE.md "Subscriptions".
- `debugPrint` (573 calls) is the logging style; only `print(` (0 calls) and per-item
  `developer.log` in parsing are findings.
- Hard casts in DTOs (`as String`) are house style even when a reviewer calls them strict
  (PR414 discussion); `as T?` only for contract-nullable fields.
- `unawaited(...)`, `always_put_control_body_on_new_line` and `unawaited_futures` infos are
  house idioms.
- Test doubles are hand-written fakes, often with `noSuchMethod` (36 test files). There is no
  mocktail, mockito or bloc_test dependency.
- Cubits only: 74 `Cubit` classes, 0 `Bloc` classes. Do not ask to convert a Cubit to a Bloc.

## Legacy, do not report unless the PR touches the exact lines
- Raw colours and sizes: 586 `Colors.*` hits in 132 files, 222 `Color(0x` in 32 files,
  396 `ThemeColors.` (deprecated) in 79 files, 165 literal `fontSize:` in 63 files.
- 297 comment blocks over 5 lines in 175 files.
- `getIt<...>()` called from widgets in 57 files under `presentation/widgets/`.
- 120 `presentation/` files import `lib/common/`; 42 import `lib/services/`.
- `AppL10n.current` (global l10n lookup) in 21 files, incl. older `Failure` messages. New failures use
  `FailureReason` (`lib/core/error/failures.dart`, MNT-1062), translated when read.
- Deprecated widget still in use: `TextViewWidget` (`lib/common/widgets/custom_text.dart`, 46 files).
- Ticket references in code comments (`MNT-xxx`, 176 hits).

## Unsettled conventions: do not enforce either way
- Where pure, framework-free helpers live: `core/util/helpers`, `core/validators` or
  `domain/services` (reviewers disagreed on PR320, PR415).
- Domain entities importing `dart:ui` / `vector_math` (accepted with a justification on PR320).
- Caret ranges for direct dependencies (all deps use `^`); pinning is raised only when code relies
  on version-specific behaviour.
