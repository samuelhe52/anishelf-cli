#!/usr/bin/env python3
"""Install and verify the AniShelf CLI for agent workflows."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass

DEFAULT_REPO_URL = "git+https://github.com/samuelhe52/anishelf-cli.git"
DEFAULT_REF = "v0.1.1"


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install anishelf-cli as a uv tool and verify the ani command."
    )
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL, help="Git or local package URL.")
    parser.add_argument(
        "--ref",
        default=DEFAULT_REF,
        help="Git ref to append to repo URLs that do not already include one.",
    )
    parser.add_argument(
        "--python",
        default="3.13",
        help="Python version passed to `uv tool install --python`.",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Only verify that an existing ani command is available.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Pass --force to uv tool install.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the commands that would run without installing.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON result envelope.",
    )
    return parser.parse_args()


def package_source(repo_url: str, ref: str) -> str:
    if not ref:
        return repo_url
    if repo_url.startswith("git+") and "@" not in repo_url.removeprefix("git+"):
        return f"{repo_url}@{ref}"
    return repo_url


def run(command: list[str]) -> CommandResult:
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    return CommandResult(
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def command_to_dict(result: CommandResult) -> dict[str, object]:
    return {
        "command": result.command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def print_result(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return

    status = payload.get("status", "unknown")
    print(f"status: {status}")
    for command in payload.get("commands", []):
        if isinstance(command, dict):
            rendered = " ".join(str(part) for part in command.get("command", []))
            print(f"command: {rendered}")
            print(f"returncode: {command.get('returncode')}")


def main() -> int:
    args = parse_args()

    install_cmd = [
        "uv",
        "tool",
        "install",
        "--python",
        args.python,
    ]
    if args.force:
        install_cmd.append("--force")
    install_cmd.append(package_source(args.repo_url, args.ref))
    verify_cmd = ["ani", "--help"]

    if args.dry_run:
        print_result(
            {
                "status": "dry-run",
                "commands": [
                    {"command": install_cmd, "returncode": None},
                    {"command": verify_cmd, "returncode": None},
                ],
            },
            args.json,
        )
        return 0

    commands: list[CommandResult] = []
    if not args.skip_install:
        if shutil.which("uv") is None:
            print_result(
                {
                    "status": "missing-uv",
                    "error": "`uv` is required. Install uv, then rerun this script.",
                    "commands": [],
                },
                args.json,
            )
            return 127
        install_result = run(install_cmd)
        commands.append(install_result)
        if install_result.returncode != 0:
            print_result(
                {"status": "install-failed", "commands": [command_to_dict(install_result)]},
                args.json,
            )
            return install_result.returncode

    if shutil.which("ani") is None:
        print_result(
            {
                "status": "ani-not-on-path",
                "error": (
                    "ani was not found on PATH. Run `uv tool update-shell` and open a new shell."
                ),
                "commands": [command_to_dict(command) for command in commands],
            },
            args.json,
        )
        return 127

    verify_result = run(verify_cmd)
    commands.append(verify_result)
    status = "ok" if verify_result.returncode == 0 else "verify-failed"
    print_result(
        {"status": status, "commands": [command_to_dict(command) for command in commands]},
        args.json,
    )
    return verify_result.returncode


if __name__ == "__main__":
    sys.exit(main())
