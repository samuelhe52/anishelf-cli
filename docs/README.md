# Documentation Map

These documents are routing aids for implementation work, not a replacement for
reading the code.

- `north-star.md`: Durable product direction, safety constraints, and decisions
  that should not drift casually.
- `implementation-state.md`: Lightweight snapshot of what the repo currently
  implements and the likely next work.
- `ux-evaluation.md`: Hands-on from-scratch UX evaluation (human + agent
  perspectives) with prioritized optimization recommendations.
- `reference/cloudkit-auth.md`: CloudKit login, web auth token execution,
  locking, retries, and redaction notes.
- `reference/cloudkit-app-auth.md`: Embedded public app auth, environment
  overrides, redaction, invalidation diagnostics, and rotation notes.
- `reference/anishelf-domain.md`: AniShelf CloudKit schema, identities, cache,
  batch, export, and TMDb hydration notes.

When adding docs, prefer updating one of these files unless the topic has a
clearly separate lifecycle.
