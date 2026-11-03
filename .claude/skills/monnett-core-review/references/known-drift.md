# Known drift: where CLAUDE.md and the code disagree

Measured on `develop` 2026-11-03 (`cff45faf`) unless a line gives another date. Use this to avoid false
findings. `pr/REVIEW.md` wins where this file disagrees with it.

## CLAUDE.md is stale here: trust the code
- The package tree in CLAUDE.md lists classes that do not exist (e.g. `FeedQueryRepository`,
  `CommentStatsRepository`, `BlockService`, `RelationshipService`, `OidcService`, `PasswordService`,
  `MediaService`, `infra/RateLimiter`). Do not ask the author to use them.
- Packages CLAUDE.md omits are legitimate: `i18n/`, `service/ext/*` (integration adapters),
  `service/subscription`, `service/entitlement`, `service/email`, `remoteconfig`,
  `api/v1/{webhook,share,public_}`, `api/v1/*/mappers`.
- `HmacHttpAuthMechanism` is the main JWT mechanism for all endpoints (HS256), not only internal/admin.
- Extra datasources exist: `matrix-keys` (raw JDBC to Synapse DB) and `monitor` (JobRunr leader election).
- Async events go through `service/common/events/EventPublisher` (fires after commit). CLAUDE.md
  still describes `Event<T>`. Legacy `Event<T>` injectors: `AlgorithmControlService`,
  `AdminTierLimitService`, `AnalyticsExclusionService`, `AnalyticsPersonService`, `MediaFileService`,
  `RemoteConfigService`.

## Legitimate exceptions to rules
- `javax.crypto`, `javax.imageio`, `javax.sql`, `javax.net` are JDK packages, not Jakarta EE.
- Webhook resources (`api/v1/webhook/*`) and public/share endpoints return protocol 4xx/5xx and verify
  signatures in code; `@PermitAll` there is expected.
- New v1 → v0 dependencies other than `ResponseUtil` and `MinimalUserDTO` are a `nit` at most
  (`core-architecture` § 4; 62 v1 files import v0).
- Services in `service/ext/*` and REST clients or filters may use `jakarta.ws.rs` types.
- Test doubles in `src/test/java/com/monnet/social/core/testdoubles/`, wired in
  `application-test.properties`: `FakePushGateway`, `FakeBrevoContactClient`,
  `FakeSynapseAdminService`, `FakeOidcTokenVerifier`, `FakeObjectStorageService`, `FakeVipsRunner`,
  `FakeFfmpegRunner`, `FakeFirebaseRemoteConfigClient`, `FakeEmailSender`, `FakeTemplateRenderer`,
  `FakeFileUrlBuilder`, `FakeSygnalServer`, `CapturingAlertRouter`, the `SynapseMock*Server`s and the
  analytics `Test*` doubles. A finding about an internal mock accepts a stated reason.
- feed, stats and subscription read other features' tables (`core-architecture` § 1 interim rule).

## Legacy, do not report unless the PR touches the exact lines
- 89 `SuCoMe.fail`/`error` calls with string codes; 38 files with `@UserIdClaimV1` (Long id): the
  public UUID is preferred for new code only.
- 115 test files with mocks (sanctioned ones included); 248 test files with `@Transactional`.
- 543 Javadocs over 5 lines and 60 public nested types (measured 2026-10-07).
- Missing Bruno requests and `@Operation` on existing admin endpoints.
- God classes (`PostRepository`, `UserRelationshipService`, `SubscriptionService`, `PostService`):
  adding to them is normal; flag only a new responsibility that clearly belongs elsewhere.
