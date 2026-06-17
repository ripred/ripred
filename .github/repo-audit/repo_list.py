#!/usr/bin/env python3
"""Manage the repository manifest used by the maintenance scripts."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_OWNER = "ripred"
MANIFEST_PATH = Path(__file__).with_name("repositories.json")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?$")


def run_json(command: list[str]) -> Any:
    """Run a command that returns JSON."""
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return json.loads(result.stdout)


def load_manifest(path: Path) -> dict[str, Any]:
    """Load the repository manifest."""
    if not path.exists():
        return {
            "version": 1,
            "description": "Repository manifest used by ripred maintenance scripts.",
            "repositories": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write the repository manifest in a stable order."""
    manifest["repositories"] = sorted(
        manifest.get("repositories", []),
        key=lambda item: (item["owner"].lower(), item["name"].lower()),
    )
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def split_repo(value: str) -> tuple[str, str]:
    """Parse OWNER/NAME or NAME into owner and name."""
    if not REPOSITORY_RE.fullmatch(value):
        raise SystemExit(f"invalid repository parameter: {value!r}")
    if "/" in value:
        owner, name = value.split("/", 1)
        return owner, name
    return DEFAULT_OWNER, value


def repo_key(repo: dict[str, Any]) -> tuple[str, str]:
    """Return a normalized manifest key."""
    return repo["owner"].lower(), repo["name"].lower()


def fetch_repo(owner: str, name: str) -> dict[str, Any]:
    """Fetch repository metadata from GitHub."""
    data = run_json(
        [
            "gh",
            "repo",
            "view",
            f"{owner}/{name}",
            "--json",
            "name,isPrivate,visibility,description,defaultBranchRef,url,licenseInfo",
        ]
    )
    return {
        "owner": owner,
        "name": data["name"],
        "default_branch": (data.get("defaultBranchRef") or {}).get("name") or "main",
        "url": data.get("url") or f"https://github.com/{owner}/{data['name']}",
        "private": bool(data.get("isPrivate")),
        "visibility": data.get("visibility")
        or ("PRIVATE" if data.get("isPrivate") else "PUBLIC"),
        "description": data.get("description") or "",
        "license_key": (data.get("licenseInfo") or {}).get("key"),
    }


def list_repositories(args: argparse.Namespace) -> None:
    """Print the current manifest list."""
    manifest = load_manifest(args.manifest)
    repositories = manifest.get("repositories", [])
    if args.json:
        print(json.dumps(repositories, indent=2, sort_keys=True))
        return
    for repo in repositories:
        private = "private" if repo.get("private") else "public"
        print(f"{repo['owner']}/{repo['name']}\t{repo['default_branch']}\t{private}")


def add_repository(args: argparse.Namespace) -> None:
    """Add or update one repository in the manifest."""
    manifest = load_manifest(args.manifest)
    owner, name = split_repo(args.repository)
    new_repo = fetch_repo(owner, name)
    repositories = manifest.setdefault("repositories", [])
    existing = {repo_key(repo): index for index, repo in enumerate(repositories)}
    key = repo_key(new_repo)
    if key in existing:
        if not args.update:
            raise SystemExit(f"{owner}/{name} is already in {args.manifest}")
        repositories[existing[key]] = new_repo
    else:
        repositories.append(new_repo)
    save_manifest(args.manifest, manifest)
    print(f"stored {new_repo['owner']}/{new_repo['name']}")


def remove_repository(args: argparse.Namespace) -> None:
    """Remove one repository from the manifest."""
    manifest = load_manifest(args.manifest)
    owner, name = split_repo(args.repository)
    key = (owner.lower(), name.lower())
    repositories = manifest.get("repositories", [])
    kept = [repo for repo in repositories if repo_key(repo) != key]
    if len(kept) == len(repositories):
        raise SystemExit(f"{owner}/{name} is not in {args.manifest}")
    manifest["repositories"] = kept
    save_manifest(args.manifest, manifest)
    print(f"removed {owner}/{name}")


def contains_repository(args: argparse.Namespace) -> None:
    """Return whether a repository is present in the manifest."""
    manifest = load_manifest(args.manifest)
    owner, name = split_repo(args.repository)
    key = (owner.lower(), name.lower())
    found = any(repo_key(repo) == key for repo in manifest.get("repositories", []))
    print("yes" if found else "no")
    raise SystemExit(0 if found else 1)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="print repositories")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=list_repositories)

    add_parser = subparsers.add_parser("add", help="add a repository")
    add_parser.add_argument("repository", help="repository name or OWNER/NAME")
    add_parser.add_argument("--update", action="store_true")
    add_parser.set_defaults(func=add_repository)

    remove_parser = subparsers.add_parser("remove", help="remove a repository")
    remove_parser.add_argument("repository", help="repository name or OWNER/NAME")
    remove_parser.set_defaults(func=remove_repository)

    contains_parser = subparsers.add_parser("contains", help="test membership")
    contains_parser.add_argument("repository", help="repository name or OWNER/NAME")
    contains_parser.set_defaults(func=contains_repository)
    return parser


def main() -> None:
    """Run the command-line interface."""
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
