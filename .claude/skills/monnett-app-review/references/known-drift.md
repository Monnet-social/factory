# Known drift: where CLAUDE.md and the code disagree (monnett-app)

Measured on `develop` 2026-11-03 (`68d1e279`) unless a line gives another date. Use this to avoid
false findings. `pr/REVIEW.md` wins where this file disagrees with it.

## CLAUDE.md is stale here: trust the code
- CLAUDE.md names `lib/providers/` as the Riverpod legacy: it does not exist. Riverpod survives in
  8 files (`lib/main.dart`, `lib/common/widgets/`, `lib/views/`, `lib/presentation/widgets/components/`).
  `lib/cubits/` holds 1 file. Real legacy dirs: `lib/common/` (21 files, imported by 130),
  `lib/services/` (10, imported by 51), `lib/controllers/` (13, incl. Matrix
  `outgoing_queue_controller.dart`, imported by 22), `lib/views/` (5). `lib/legacy/` no longer exists.
- CLAUDE.md lists `presentation/widgets/scaffolds/`: it does not exist. Do not ask to move files there.
  The target tree also omits folders that are legitimate and in use: `core/{deeplink, enums, error,
  init, l10n, media, notifications, router, validators}`, `data/mappers/`, `presentation/post_viewer/`,
  `presentation/widgets/{common, components, screens, templates}`.
- `domain/repositories/` holds the concrete implementations, which import `data/` (18 files). This is
  the design, not a violation.
- "`data/` depends only on `core/`": 38 `data/` files import `domain/` entities (return types of API
  clients). Reviewers agreed (PR438) not to enforce it per PR; only business decisions inside an API
  client are a finding.
- `core/` imports `presentation/` in 29 files (navigation helper, notification navigator, init).
  Flag only new imports in new `core/` files.
- `lib/domain/entities/` has `fromJson` in 25 files (entities parsing JSON, e.g. `MediaPost`). New DTOs
  go to `data/data_sources/dtos/*_dto.dart`; do not ask to migrate existing entities.
- "Every DTO ships a `fromJson` test": 24 DTO files, 17 DTO test files. Applies to new DTOs.
- CI time zones: `test/core/util` and the two chat timeline tests under
  `test/presentation/widgets/screens/chat/widgets/` run under `TZ=Europe/Luxembourg`,
  `notification_dto_test.dart` under `TZ=Pacific/Honolulu`; every other test runs in UTC.
- Toolchain: CI and Codemagic build with Flutter 3.41.9; CLAUDE.md and some code comments reason about
  3.47 behaviour. A plugin fix that needs a newer Flutter may resolve to an older version in CI
  (PR490 camerax).
- No code generation (no build_runner, freezed, json_serializable). DTOs and mappers are hand-written;
  the only generated files are the gitignored l10n classes.

## Legitimate exceptions to rules
- `// ignore: monnett_lints/hardcoded_string` with a reason for numbers and symbols (10 files).
  `// l10n-ignore-next-line` is also accepted by the lint.
- `Navigator.push(MaterialPageRoute(...))` for value-returning modal editors (crop editor) instead of
  `AppRoutes`.
- `AppEnv.isProd` in Remote Config defaults and RevenueCat config (`config.dart`, `firebase.dart`,
  `revenuecat_config.dart`, `app_config_state.dart`): documented in CLAUDE.md "Subscriptions".
- `debugPrint` (575 calls) is the logging style; only `print(` (0 calls) and per-item `developer.log`
  in parsing are findings.
- Hard casts in DTOs (`as String`) are house style even when a reviewer calls them strict (PR414);
  `as T?` only for contract-nullable fields.
- `unawaited(...)`, `always_put_control_body_on_new_line` and `unawaited_futures` infos are house idioms.
- Test doubles are hand-written fakes, often with `noSuchMethod` (67 test files).
- Cubits only: 79 `Cubit` classes, 0 `Bloc` classes. Do not ask to convert a Cubit to a Bloc.

## Legacy, do not report unless the PR touches the exact lines
- Raw colours and sizes: 570 `Colors.*` hits in 124 files, 218 `Color(0x` in 26 files, 324
  `ThemeColors.` (deprecated) in 74 files, 144 literal `fontSize:` in 59 files.
- 297 comment blocks over 5 lines in 175 files (measured 2026-10-12).
- `getIt<...>()` called from widgets in 66 files under `presentation/widgets/`.
- 113 `presentation/` files import `lib/common/`; 41 import `lib/services/`.
- `AppL10n.current` (global l10n lookup) in 21 files, incl. older `Failure` messages. New failures use
  `FailureReason` (`lib/core/error/failures.dart`, MNT-1062).
- Deprecated `TextViewWidget` (`lib/common/widgets/custom_text.dart`) in 42 files.
- Ticket references in code comments (`MNT-xxx`, 205 hits).
