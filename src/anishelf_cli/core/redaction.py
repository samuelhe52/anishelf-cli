from __future__ import annotations

import re
from dataclasses import dataclass, field

SENSITIVE_QUERY_KEYS = (
    "api_key",
    "apiKey",
    "ckAPIToken",
    "ckWebAuthToken",
    "token",
)


@dataclass(slots=True)
class SecretRedactor:
    _secrets: dict[str, str] = field(default_factory=dict)

    def register(self, secret: str | None, label: str) -> None:
        if not secret:
            return
        self._secrets[secret] = f"<redacted:{label}>"

    def redact(self, text: str) -> str:
        redacted = text
        for secret, placeholder in sorted(
            self._secrets.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            redacted = redacted.replace(secret, placeholder)

        for key in SENSITIVE_QUERY_KEYS:
            pattern = rf"({re.escape(key)}=)([^\s&]+)"
            redacted = re.sub(pattern, rf"\1<redacted:{key}>", redacted)

        return redacted


def redact_text(text: str, secrets: dict[str, str] | None = None) -> str:
    redactor = SecretRedactor()
    if secrets:
        for secret, label in secrets.items():
            redactor.register(secret, label)
    return redactor.redact(text)
