# CloudKit Auth And Execution

This document preserves implementation reference notes for CloudKit access. It
is not a user setup guide.

## Endpoint Shape

CloudKit Web Services database v1 endpoints are rooted at:

```text
https://api.apple-cloudkit.com/database/1/<container>/<environment>/<database>/<operation>
```

The default AniShelf scope is:

- Container: `iCloud.com.samuelhe.MyAnimeList`
- Environment: `production`
- Database: `private`

Private database requests use app auth plus a user-scoped `ckWebAuthToken`. The
web auth token is rolling auth state: successful token-consuming requests can
return a successor token, and the previous token should be treated as stale
after that request.

## Login Flow

Production login should assume an HTTPS callback URL. The production-safe flow
is:

1. Start login by probing a private database endpoint with app auth only.
2. Read CloudKit's authentication redirect URL from the response.
3. Send the user through browser sign-in.
4. Ask the user to paste the final HTTPS callback URL.
5. Extract `ckWebAuthToken` from the callback URL.
6. Store the web auth token in the OS secure credential store.

Loopback callback capture can exist for development tokens that allow localhost
callbacks, but it should remain optional. Login failures must not leave partial
web auth token state behind.

## Executor Rules

Every CloudKit request that uses `ckWebAuthToken` should go through one executor.
The executor owns:

- appending auth query parameters;
- holding a single local lock across token read, HTTP request, response parse,
  successor-token save, and error handling;
- replacing the stored web auth token before releasing the lock when CloudKit
  returns a successor token;
- clearing stored web auth state on authentication failures;
- classifying CloudKit errors into actionable CLI errors;
- retrying throttled and transient failures with bounded backoff;
- redacting tokens and callback URLs in logs, errors, and diagnostics.

Correctness requires serializing token-consuming requests. Concurrent local
commands must not reuse the same rolling web auth token.

## Error Categories

CloudKit errors should be mapped into stable CLI categories before they reach
command output:

- authentication required or failed;
- access denied;
- throttling or quota;
- conflict;
- invalid request;
- unknown item or missing zone;
- expired change token;
- transient network or server failure.

Throttling can retry within a bounded budget. Access denied should not retry
blindly. Expired zone change tokens are recoverable by discarding the affected
cursor and rebuilding from the beginning.

## Security Notes

The CLI must never print app auth, `ckWebAuthToken`, successor web auth tokens,
TMDb API keys, or raw callback URLs containing tokens. Private library data can
still appear in normal command output and exports, so structured exports should
be treated as private user data.

CloudKit requests should stay direct from the CLI to Apple. Routing private
CloudKit traffic through a relay service is outside the current design unless a
future stage intentionally reopens that trade-off.
