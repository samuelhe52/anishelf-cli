from anishelf_cli.core.redaction import SecretRedactor, redact_text


def test_redacts_registered_cloudkit_and_tmdb_tokens() -> None:
    redactor = SecretRedactor()
    cloudkit_token = "test-cloudkit-token-value"
    redactor.register(cloudkit_token, "cloudkit-api-token")
    redactor.register("7024c3b1daa8c5ce9eaa87c13219b012", "tmdb-api-key")

    output = redactor.redact(
        f"ckAPIToken={cloudkit_token} api_key=7024c3b1daa8c5ce9eaa87c13219b012"
    )

    assert cloudkit_token not in output
    assert "7024c3b1daa8c5ce9eaa87c13219b012" not in output
    assert "ckAPIToken=<redacted:ckAPIToken>" in output
    assert "api_key=<redacted:api_key>" in output


def test_redacts_callback_urls_even_without_registered_secret() -> None:
    text = "paste https://example.com/callback?ckWebAuthToken=abc123&foo=bar&ckAPIToken=def456"

    output = redact_text(text)

    assert "abc123" not in output
    assert "def456" not in output
    assert "ckWebAuthToken=<redacted:ckWebAuthToken>" in output
    assert "ckAPIToken=<redacted:ckAPIToken>" in output
