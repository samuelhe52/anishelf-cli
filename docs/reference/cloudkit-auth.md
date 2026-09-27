# CloudKit Auth Reference

Developer reference for CloudKit login and authenticated request execution. This
is not a user setup guide.

## Endpoint Shape

CloudKit Web Services database v1 endpoints are rooted at:

```text
https://api.apple-cloudkit.com/database/1/<container>/<environment>/<database>/<operation>
```

The default AniShelf scope is container
`iCloud.com.samuelhe.MyAnimeList`, environment `production`, database
`private`.

Private database requests use app auth plus a user-scoped `ckWebAuthToken`.
Successful token-consuming requests can return successor auth state, so local
commands must not reuse the same rolling web auth token concurrently.

## Login

The production-safe login path is browser sign-in followed by manual paste of
the final HTTPS callback URL. The CLI extracts `ckWebAuthToken` from that URL and
stores only the token in the configured secret backend.

The default backend is the OS secure credential store. Headless users can
explicitly opt into `plaintext-file` when the OS backend cannot unlock, but this
stores CloudKit auth tokens unencrypted on disk and must be presented as a
security downgrade in user-facing output.

Only one local CloudKit login is supported at a time. If a user is already
logged in, `auth login` should fail until `auth logout` clears the stored token.
`auth logout` should also clear all local library cache files so a later login
cannot reuse another user's cache.

Loopback callback capture is available for development-style flows that allow a
localhost callback. It must validate loopback hosts and must not expose callback
URLs in normal output.

## Executor Rules

Authenticated CloudKit requests should go through a shared executor. The
executor owns:

- adding app and web auth query parameters;
- holding a local lock across token read, HTTP request, response parse,
  successor-token save, and auth-failure cleanup;
- replacing the stored web auth token when a successful response returns
  successor state, read from the `X-Apple-CloudKit-Web-Auth-Token` response
  header (where production CloudKit returns it) or, as a fallback, a
  `webAuthToken`/`ckWebAuthToken` body key;
- clearing stored user auth state on authentication failures;
- classifying errors into actionable CLI failures;
- redacting tokens and callback URLs in errors and diagnostics.

Retry behavior should be bounded and reserved for transient or throttled
requests. Access denied and user-auth failures should not retry blindly.

Because web auth tokens roll, a request that reached CloudKit may already have
consumed the token it carried. Production CloudKit has been observed to keep
accepting a predecessor whose successor went unused, but that is not documented
behavior, and if a replayed token were rejected the auth failure would clear the
saved login. The executor therefore retries only
connection-phase failures (`httpx.ConnectError` and `httpx.ConnectTimeout`,
including TLS handshake errors), where the request never left the machine: up to
four attempts with a short linear backoff. Read errors, timeouts after sending,
and HTTP error responses are surfaced immediately with the transport error class
and a network hint.

Under `--verbose` the executor logs, without any token values: how long it
waited for the token lock; each response's status, attempt, elapsed time, and
`X-Apple-Request-UUID` (as `requestId`, which Apple can trace); and the token
outcome (`rolled forward`, `kept`, `successor ignored` on an error response, or
`cleared` after an authentication failure). Other response headers are never
logged, because `X-Apple-CloudKit-Web-Auth-Token` carries the successor token.

## Security

Never print app auth, `ckWebAuthToken`, successor web auth tokens, TMDb API keys,
or raw callback URLs containing tokens. Private library data can appear in
normal command output and exports, so exports should be treated as private user
data.
