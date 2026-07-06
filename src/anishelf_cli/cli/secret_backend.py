from __future__ import annotations

import sys

import typer

from anishelf_cli import config
from anishelf_cli.models import SecretBackend


def plaintext_backend_warning() -> str:
    return (
        "Security warning: plaintext-file stores CloudKit auth tokens and TMDb API keys "
        f"unencrypted at {config.plaintext_keyring_file()}. Any process or user that can read "
        "that file can use those secrets."
    )


def enable_plaintext_backend() -> config.SecretDefaults:
    updated = config.SecretDefaults(backend=SecretBackend.PLAINTEXT_FILE)
    config.save_secret_defaults(updated)
    return updated


def system_backend_unavailable_guidance(
    exc: Exception,
    *,
    action: str,
) -> str:
    return (
        f"System secure credential storage is unavailable for {action}: {exc}\n\n"
        'ani is still configured for secrets.backend = "system". '
        "To use unencrypted plaintext-file storage instead, run:\n\n"
        "  ani config set-secrets-backend plaintext-file\n\n"
        "Then rerun the command."
    )


def confirm_plaintext_backend(message: str) -> bool:
    typer.echo(f"{message} [y/N]: ", nl=False, err=True)
    answer = sys.stdin.readline().strip().lower()
    typer.echo("", err=True)
    return answer in {"y", "yes"}
