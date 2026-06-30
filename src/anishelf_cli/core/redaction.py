from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit

SENSITIVE_QUERY_KEYS = (
    "api_key",
    "apiKey",
    "access_token",
    "authToken",
    "auth_token",
    "ckAPIToken",
    "ckWebAuthToken",
    "refresh_token",
    "session",
    "session_id",
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
        redacted = re.sub(r"https?://[^\s]+", self._redact_sensitive_url_match, redacted)

        for secret, placeholder in sorted(
            self._secrets.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            redacted = redacted.replace(secret, placeholder)

        for key in SENSITIVE_QUERY_KEYS:
            pattern = rf"({re.escape(key)}=)([^\s&]+)"
            redacted = re.sub(pattern, rf"\1<redacted:{key}>", redacted)
            pattern = rf'("{re.escape(key)}"\s*:\s*")([^"]+)(")'
            redacted = re.sub(pattern, rf"\1<redacted:{key}>\3", redacted)
            pattern = rf"('{re.escape(key)}'\s*:\s*')([^']+)(')"
            redacted = re.sub(pattern, rf"\1<redacted:{key}>\3", redacted)

        return redacted

    def _redact_sensitive_url_match(self, match: re.Match[str]) -> str:
        url = match.group(0)
        if any(secret in url for secret in self._secrets):
            return "<redacted:sensitive-url>"

        parsed = urlsplit(url)
        query_items = parse_qsl(parsed.query, keep_blank_values=True)
        fragment_items = parse_qsl(parsed.fragment, keep_blank_values=True)
        sensitive_keys = {key.lower() for key in SENSITIVE_QUERY_KEYS}
        if any(key.lower() in sensitive_keys for key, _ in (*query_items, *fragment_items)):
            return "<redacted:sensitive-url>"
        return url


def redact_text(text: str, secrets: dict[str, str] | None = None) -> str:
    redactor = SecretRedactor()
    if secrets:
        for secret, label in secrets.items():
            redactor.register(secret, label)
    return redactor.redact(text)
