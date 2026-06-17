#!/usr/bin/env python3
"""Run repo_audit_update.py over repositories from the manifest."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = SCRIPT_DIR / "repositories.json"
AUDIT_SCRIPT = SCRIPT_DIR / "repo_audit_update.py"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?$")
TARGET_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def load_manifest(path: Path) -> list[dict[str, Any]]:
    """Load manifest repositories."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("repositories", [])


def validate_repo_parameter(value: str) -> None:
    """Reject malformed repository parameters before any action runs."""
    if not REPOSITORY_RE.fullmatch(value):
        raise SystemExit(f"invalid repository parameter: {value!r}")


def validate_path_parameter(value: str) -> None:
    """Reject unsafe repository-relative folder paths."""
    if value != value.strip() or "\\" in value or not TARGET_PATH_RE.fullmatch(value):
        raise SystemExit(f"invalid target path parameter: {value!r}")
    parts = value.split("/")
    if not parts or any(part in {"", ".", "..", ".git"} for part in parts):
        raise SystemExit(f"target path contains an unsafe segment: {value!r}")


def run_for_repo(repo: dict[str, Any], args: argparse.Namespace) -> int:
    """Run the audit script for one repository."""
    full_name = f"{repo['owner']}/{repo['name']}"
    command = [
        sys.executable,
        str(AUDIT_SCRIPT),
        "--manifest",
        str(args.manifest),
        "--output-dir",
        str(args.output_dir),
        args.command,
        "--repo",
        full_name,
    ]
    if args.path and args.command in {"plan", "apply"}:
        command.extend(["--path", args.path])
    if args.keep_worktrees and args.command in {"plan", "apply"}:
        command.append("--keep-worktrees")
    print(f"== {full_name} ==")
    result = subprocess.run(command, text=True, timeout=args.timeout)
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "apply", "verify-actions", "security-alerts"])
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    parser.add_argument("--repo", action="append", help="limit to NAME or OWNER/NAME")
    parser.add_argument("--path", help="repository-relative folder target for plan/apply")
    parser.add_argument("--keep-worktrees", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    return parser


def main() -> None:
    """Run the manifest orchestration."""
    args = build_parser().parse_args()
    repositories = load_manifest(args.manifest)
    if args.path:
        validate_path_parameter(args.path)
    if args.repo:
        for value in args.repo:
            validate_repo_parameter(value)
        wanted = {value.lower() for value in args.repo}
        repositories = [
            repo
            for repo in repositories
            if repo["name"].lower() in wanted
            or f"{repo['owner']}/{repo['name']}".lower() in wanted
        ]
        missing = wanted - {
            repo["name"].lower() for repo in repositories
        } - {f"{repo['owner']}/{repo['name']}".lower() for repo in repositories}
        if missing:
            raise SystemExit("repository is not in manifest: " + ", ".join(sorted(missing)))
    failures = 0
    for repo in repositories:
        failures += 1 if run_for_repo(repo, args) else 0
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
