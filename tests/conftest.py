from __future__ import annotations

from pathlib import Path

import pytest

_REAL_USER_CONFIG_FILE = Path.home() / ".anishelf-cli" / "config.toml"


def _is_real_user_config_file(path: Path) -> bool:
    return path == _REAL_USER_CONFIG_FILE


@pytest.fixture(autouse=True)
def isolate_user_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    original_exists = Path.exists
    original_open = Path.open
    original_read_text = Path.read_text
    original_write_text = Path.write_text
    original_unlink = Path.unlink

    def reject_real_config(path: Path) -> None:
        if _is_real_user_config_file(path):
            raise AssertionError(
                f"Tests must not read or write the real user config file: {path}"
            )

    def guarded_exists(path: Path) -> bool:
        reject_real_config(path)
        return original_exists(path)

    def guarded_open(path: Path, *args: object, **kwargs: object):
        reject_real_config(path)
        return original_open(path, *args, **kwargs)

    def guarded_read_text(path: Path, *args: object, **kwargs: object) -> str:
        reject_real_config(path)
        return original_read_text(path, *args, **kwargs)

    def guarded_write_text(path: Path, *args: object, **kwargs: object) -> int:
        reject_real_config(path)
        return original_write_text(path, *args, **kwargs)

    def guarded_unlink(path: Path, *args: object, **kwargs: object) -> None:
        reject_real_config(path)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", guarded_exists)
    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    monkeypatch.setattr(Path, "write_text", guarded_write_text)
    monkeypatch.setattr(Path, "unlink", guarded_unlink)
    monkeypatch.delenv("ANISHELF_CLI_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ANISHELF_CLI_CACHE_DIR", raising=False)
    monkeypatch.delenv("ANISHELF_CLI_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
