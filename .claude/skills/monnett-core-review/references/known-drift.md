# Known drift: where CLAUDE.md and the code disagree

Measured on `develop` 2026-10-07, updated 2026-10-29. Use this to avoid false findings.

## CLAUDE.md is stale here — trust the code
- The package tree in CLAUDE.md lists ~33 classes that do not exist (e.g. `FeedQueryRepository`,
  `CommentStatsRepository`, `BlockService`, `RelationshipService`, `OidcService`, `PasswordService`,
  `MediaService`, `infra/RateLimiter`). Do not ask the author to use them.
- Packages CLAUDE.md omits are legitimate: `i18n/`, `service/ext/*` (integration adapters),
  `service/subscription`, `service/entitlement`, `service/email`, `remoteconfig`,
  `api/v1/{webhook,share,public_}`, `api/v1/*/mappers`.
- `HmacHttpAuthMechanism` is the main JWT mechanism for all endpoints (HS256), not only internal/admin.
- Extra datasources exist: `matrix-keys` (raw JDBC to Synapse DB) and `monitor` (JobRunr leader election).
- Async events go through `service/common/events/EventPublisher` (fires after commit). CLAUDE.md
  still describes `Event<T>`; `core-architecture` § 3 forbids new `Event<T>` injection. Legacy
  injectors: `AlgorithmControlService`, `AdminTierLimitService`, `MediaFileService`,
  `RemoteConfigService`.

## Legitimate exceptions to rules
- `javax.crypto`, `javax.imageio`, `javax.sql`, `javax.net` are JDK packages, not Jakarta EE — allowed.
- Webhook resources (`api/v1/webhook/*`) and public/share endpoints return protocol 4xx/5xx and verify
  signatures in code; `@PermitAll` there is expected.
- `ResponseUtil` lives in `api.v0.mappers` and is used by all v1 resources; importing it is fine, as is
  the shared v0 user row `MinimalUserDTO`. Other new v1 → v0 dependencies are a `nit` at most
  (`core-architecture` § 4; 62 v1 files import v0 today).
- Services in `service/ext/*` and REST clients/filters may use `jakarta.ws.rs` types.
- Test doubles for external systems live in `src/test/java/com/monnet/social/core/testdoubles/`
  (wired in `application-test.properties`): `FakePushGateway`, `FakeBrevoContactClient`,
  `FakeSynapseAdminService`, `FakeOidcTokenVerifier`, `FakeObjectStorageService`, `FakeVipsRunner`,
  `FakeFfmpegRunner`, `FakeFirebaseRemoteConfigClient`, `FakeEmailSender`, `FakeSygnalServer`, the
  `SynapseMock*Server`s. Mocking `AsyncJobScheduler` is sanctioned. Report an internal mock only
  with the double to use named, and accept a stated reason.
- feed, stats and subscription read other features' tables (`core-architecture` § 1 interim rule).

## Legacy, do not report unless the PR touches the exact lines
- 543 Javadocs over 5 lines; 60 public nested types; 175 `SuCoMe` calls with string codes.
- 96 test files with mocks (sanctioned ones included); 241 test files with `@Transactional`.
- Migration names V0001–V0051: frozen and legitimate; only new files use the timestamp scheme.
- v1 resources using `@UserIdClaimV1` (Long id) — the public UUID is preferred for new code only.
- Missing Bruno requests for existing admin endpoints; missing `@Operation` on existing admin endpoints.
- God classes (`PostRepository`, `UserRelationshipService`, `SubscriptionService`, `PostService`):
  adding to them is normal; flag only a new responsibility that clearly belongs elsewhere.

## Unsettled conventions — do not enforce either way
- `Dto` vs `DTO` suffix (both in use; new code drifts to `Dto`).
- Records with Lombok `@Builder` (common) vs SPDD norm "records without Lombok".
- Comments that name the ticket (SPDD norm) vs CLAUDE.md "don't narrate history".
