# Collection UI regression repair — 2026-09-09

## Scope

Restore collection progress dialog in the Cloudflare trial. Existing injected capture handler bypassed the original popup and rendered all source diagnostics inline. No collector, credential, recipient, or mail scheduling logic changed.

- Native modal dialog with focus containment, close/Escape, reduced-motion and mobile sizing.
- Closing does not cancel polling or server work; reopening and page reload reuse the job ID.
- Actual terminal-task percentage, separate category processing counts, collapsed source errors.
- Observation/network failure is not reported as successful collection or verified zero.
- `keep_names: false` prevents the deployment bundler from injecting Worker-only name helpers into serialized browser functions.

## Verification

- Node regression suite: 31 tests, including category metadata, partial failure, real progress, serialized bundle configuration and existing email idempotency/scheduling tests.
- Local-only fixture: `node cloudflare/test/ui-preview.mjs`; mocked APIs, no credentials, no upstream collection or email.
- Browser: click opens popup; 3/4 completed tasks shows 75%; bids 1, news 10, laws 5 remain separate; failed source expands to a Korean error explanation.
- Close/reopen/reload: fixture metrics showed 1 start request and continued status reads, not duplicate collection requests.
- Escape: popup closes, settings stays open, focus returns to collectNow.
- Desktop and 375px viewport checked; modal clientWidth and scrollWidth both 326px, no horizontal overflow. Long Korean title uses keep-all wrapping.
- Impeccable detector: no findings. Build dry-run succeeded (sandbox-only log-path EPERM, not a compilation error).
- Deployed Worker version: `dc35916a-88e0-4760-8541-9023bf80d375`.
- Live `/cf-trial.js`: HTTP 200, popup present, no `__name` dependency. Live page reload: dialog mounted, no browser error logs.
- Deployment retains both existing weekday crons and live-mail mode. No real email or new live collection was triggered for UI QA.

## Remaining boundaries

This repairs the UI regression, not upstream TLS/timeouts. Processing totals include updates and possible duplicates; they are not unique new-notice counts. Background status observation stops after five minutes and can be resumed explicitly. Server collection deadlines are unchanged.
