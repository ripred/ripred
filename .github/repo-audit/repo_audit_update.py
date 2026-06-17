#!/usr/bin/env python3
"""Deterministic repository badge, CI, and health audit/update tool."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen


MANIFEST_PATH = Path(__file__).with_name("repositories.json")
WORK_ROOT = Path.cwd() / ".repo-maintenance-worktrees"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?$")
TARGET_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


@dataclass
class Repository:
    """Repository metadata from the manifest."""

    owner: str
    name: str
    default_branch: str
    url: str
    private: bool
    visibility: str
    description: str = ""
    license_key: str | None = None
    skip_workflows: list[str] = field(default_factory=list)
    skip_workflow_badges: list[str] = field(default_factory=list)
    skip_badge_kinds: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        """Return OWNER/NAME."""
        return f"{self.owner}/{self.name}"


@dataclass
class PlannedChange:
    """A planned or applied repository change."""

    repository: Repository
    changes: list[str] = field(default_factory=list)
    status: str = ""


@dataclass
class SecurityAlertRow:
    """A normalized security-alert report row."""

    repository: Repository
    scanner: str
    state: str
    severity: str = ""
    title: str = ""
    location: str = ""
    url: str = ""
    action: str = ""


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess."""
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result


def load_manifest(path: Path) -> list[Repository]:
    """Load repositories from the manifest."""
    data = json.loads(path.read_text(encoding="utf-8"))
    repositories = []
    for item in data.get("repositories", []):
        repositories.append(
            Repository(
                owner=item["owner"],
                name=item["name"],
                default_branch=item.get("default_branch") or "main",
                url=item.get("url") or f"https://github.com/{item['owner']}/{item['name']}",
                private=bool(item.get("private")),
                visibility=item.get("visibility") or "PUBLIC",
                description=item.get("description") or "",
                license_key=item.get("license_key"),
                skip_workflows=list(item.get("skip_workflows", [])),
                skip_workflow_badges=list(item.get("skip_workflow_badges", [])),
                skip_badge_kinds=list(item.get("skip_badge_kinds", [])),
            )
        )
    return sorted(repositories, key=lambda repo: (repo.owner.lower(), repo.name.lower()))


def fetch_repository(owner: str, name: str) -> Repository:
    """Fetch repository metadata from GitHub when not using the manifest."""
    result = run(
        [
            "gh",
            "repo",
            "view",
            f"{owner}/{name}",
            "--json",
            "name,isPrivate,visibility,description,defaultBranchRef,url,licenseInfo",
        ],
        timeout=60,
    )
    data = json.loads(result.stdout)
    return Repository(
        owner=owner,
        name=data["name"],
        default_branch=(data.get("defaultBranchRef") or {}).get("name") or "main",
        url=data.get("url") or f"https://github.com/{owner}/{data['name']}",
        private=bool(data.get("isPrivate")),
        visibility=data.get("visibility") or ("PRIVATE" if data.get("isPrivate") else "PUBLIC"),
        description=data.get("description") or "",
        license_key=(data.get("licenseInfo") or {}).get("key"),
    )


def split_repo(value: str, default_owner: str = "ripred") -> tuple[str, str]:
    """Parse OWNER/NAME or NAME."""
    if not REPOSITORY_RE.fullmatch(value):
        raise ValueError(f"invalid repository parameter: {value!r}")
    if "/" in value:
        owner, name = value.split("/", 1)
        return owner, name
    return default_owner, value


def normalize_target_path(value: str | None) -> str:
    """Validate and normalize a repository-relative folder path."""
    if not value:
        return ""
    if value != value.strip() or "\\" in value or not TARGET_PATH_RE.fullmatch(value):
        raise ValueError(f"invalid target path parameter: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError(f"target path must be repository-relative: {value!r}")
    parts = path.parts
    if not parts or any(part in {"", ".", "..", ".git"} for part in parts):
        raise ValueError(f"target path contains an unsafe segment: {value!r}")
    return PurePosixPath(*parts).as_posix()


def target_path_arg(value: str) -> str:
    """Argparse type for repository-relative folder paths."""
    try:
        return normalize_target_path(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def filter_repositories(repositories: list[Repository], names: list[str] | None) -> list[Repository]:
    """Filter repositories by NAME or OWNER/NAME."""
    if not names:
        return repositories
    selected = []
    by_full = {repo.full_name.lower(): repo for repo in repositories}
    by_name: dict[str, list[Repository]] = {}
    for repo in repositories:
        by_name.setdefault(repo.name.lower(), []).append(repo)
    errors = []
    for raw_name in names:
        try:
            owner, name = split_repo(raw_name)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if "/" in raw_name:
            repo = by_full.get(f"{owner}/{name}".lower())
            if repo is None:
                errors.append(f"repository is not in manifest: {owner}/{name}")
                continue
            selected.append(repo)
            continue
        matches = by_name.get(name.lower(), [])
        if len(matches) == 1:
            selected.append(matches[0])
        elif not matches:
            errors.append(f"repository is not in manifest: {name}")
        else:
            errors.append(f"repository name is ambiguous, use OWNER/NAME: {name}")
    if errors:
        raise SystemExit("\n".join(errors))
    unique = {repo.full_name.lower(): repo for repo in selected}
    return list(unique.values())


def selected_repositories(args: argparse.Namespace) -> list[Repository]:
    """Resolve explicit repo parameters or the manifest list."""
    manifest_repos = load_manifest(args.manifest)
    if args.repo:
        by_key = {repo.full_name.lower(): repo for repo in manifest_repos}
        by_name: dict[str, list[Repository]] = {}
        for repo in manifest_repos:
            by_name.setdefault(repo.name.lower(), []).append(repo)
        selected = []
        for value in args.repo:
            try:
                owner, name = split_repo(value)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            key = f"{owner}/{name}".lower()
            if "/" in value:
                selected.append(by_key.get(key) or fetch_repository(owner, name))
                continue
            matches = by_name.get(name.lower(), [])
            if len(matches) == 1:
                selected.append(matches[0])
            elif len(matches) > 1:
                raise SystemExit(f"repository name is ambiguous, use OWNER/NAME: {name}")
            else:
                selected.append(fetch_repository(owner, name))
        return selected
    return filter_repositories(manifest_repos, args.repositories)


def clone_repository(repo: Repository, target: Path) -> None:
    """Clone a repository into a temporary worktree."""
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    result = run(
        [
            "gh",
            "repo",
            "clone",
            repo.full_name,
            str(target),
            "--",
            "--quiet",
            "--depth",
            "1",
            "--branch",
            repo.default_branch,
        ],
        check=False,
        timeout=300,
    )
    if result.returncode == 0:
        return
    target.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-q", "-b", repo.default_branch], cwd=target)
    run(["git", "remote", "add", "origin", f"https://github.com/{repo.full_name}.git"], cwd=target)


def all_paths(repo_dir: Path) -> list[str]:
    """Return all non-git file paths."""
    paths = []
    for path in repo_dir.rglob("*"):
        if ".git" in path.parts:
            continue
        if path.is_file():
            paths.append(path.relative_to(repo_dir).as_posix())
    return sorted(paths)


def workflow_files(repo_dir: Path) -> list[str]:
    """Return active workflow files."""
    workflow_dir = repo_dir / ".github" / "workflows"
    if not workflow_dir.exists():
        return []
    return sorted(
        path.name
        for path in workflow_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}
    )


def skipped_workflow_names(repo: Repository) -> set[str]:
    """Return workflows excluded from creation and badging."""
    return {name.lower() for name in repo.skip_workflows}


def skipped_workflow_badges(repo: Repository) -> set[str]:
    """Return workflow badges excluded from README rendering."""
    return {name.lower() for name in repo.skip_workflows + repo.skip_workflow_badges}


def skipped_badge_kinds(repo: Repository) -> set[str]:
    """Return non-workflow badge kinds excluded from README rendering."""
    return {name.lower() for name in repo.skip_badge_kinds}


def read_text(path: Path) -> str:
    """Read text from a file if present."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def write_if_changed(path: Path, content: str) -> bool:
    """Write a file only if the content changed."""
    if read_text(path) == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def root_readme(repo_dir: Path) -> Path:
    """Return the root README path, preferring an existing README."""
    for path in repo_dir.iterdir():
        if path.is_file() and path.name.lower().startswith("readme"):
            return path
    return repo_dir / "README.md"


def license_path(repo_dir: Path) -> str:
    """Return the root license filename if present."""
    for path in repo_dir.iterdir():
        if path.is_file() and path.name.lower().startswith("license"):
            return path.name
    return "LICENSE"


def has_license(repo: Repository, repo_dir: Path) -> bool:
    """Return whether the repository has a license signal."""
    if repo.license_key:
        return True
    return any(path.name.lower().startswith("license") for path in repo_dir.iterdir() if path.is_file())


def root_library_name(repo_dir: Path) -> str | None:
    """Return Arduino library.properties name for root libraries."""
    library_file = repo_dir / "library.properties"
    if not library_file.exists():
        return None
    for line in read_text(library_file).splitlines():
        if line.startswith("name="):
            return line.split("=", 1)[1].strip()
    return repo_dir.name


def sketch_paths(paths: list[str]) -> list[str]:
    """Return Arduino sketch paths, ignoring fixtures and build output."""
    ignored = {"testdata", "fixtures", "fixture", "vendor", "build"}
    result = []
    for path in paths:
        lower = path.lower()
        if not lower.endswith(".ino"):
            continue
        if any(part in ignored for part in lower.split("/")):
            continue
        result.append(path)
    return result


def has_json_files(paths: list[str]) -> bool:
    """Return whether the repository has JSON files."""
    return any(path.lower().endswith(".json") for path in paths)


def has_python_files(paths: list[str]) -> bool:
    """Return whether the path list has Python files."""
    return any(path.lower().endswith(".py") for path in paths)


def scoped_paths(paths: list[str], target_path: str) -> list[str]:
    """Return paths under a repository-relative folder."""
    if not target_path:
        return paths
    prefix = target_path.rstrip("/") + "/"
    return [path[len(prefix) :] for path in paths if path.startswith(prefix)]


def display_title(value: str) -> str:
    """Return a readable title for a repository path or filename."""
    name = PurePosixPath(value).name if value else "Repository"
    words = name.replace("_", " ").replace("-", " ").split()
    return " ".join(word.upper() if word.lower() == "ci" else word.title() for word in words)


def path_slug(value: str) -> str:
    """Return a stable workflow-safe slug for a repository-relative path."""
    parts = [part for part in PurePosixPath(value).parts if part != ".github"]
    slug = "-".join(parts) if parts else "repository"
    return re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-") or "repository"


def python_workflow_name(target_path: str) -> str:
    """Return the workflow filename for a scoped Python check."""
    return f"{path_slug(target_path)}-python.yml"


def workflow_mentions_path(repo_dir: Path, workflow: str, target_path: str) -> bool:
    """Return whether a workflow file references the target folder."""
    if not target_path:
        return True
    workflow_path = repo_dir / ".github" / "workflows" / workflow
    return target_path.lower() in read_text(workflow_path).lower()


def latest_release(repo: Repository) -> str | None:
    """Return the latest GitHub release tag."""
    result = run(
        ["gh", "api", f"repos/{repo.full_name}/releases/latest", "--jq", ".tag_name"],
        check=False,
        timeout=30,
    )
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def latest_tag(repo: Repository) -> str | None:
    """Return the latest Git tag."""
    result = run(
        ["gh", "api", f"repos/{repo.full_name}/tags?per_page=1", "--jq", ".[0].name"],
        check=False,
        timeout=30,
    )
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def arduino_badge_available(name: str) -> bool:
    """Return whether ardu-badge.com has a badge for a library."""
    try:
        with urlopen(f"https://www.ardu-badge.com/badge/{quote(name)}.svg", timeout=8) as response:
            return response.status == 200 and b"<svg" in response.read(512)
    except OSError:
        return False


def visible_markdown(markdown: str) -> str:
    """Remove Markdown comments before checking visible badges."""
    return re.sub(r"<!--.*?-->", "", markdown, flags=re.S)


def has_badge_kind(
    markdown: str,
    repo: Repository,
    *,
    kind: str | None = None,
    workflow: str | None = None,
) -> bool:
    """Return whether a visible README badge already exists."""
    visible = visible_markdown(markdown).lower()
    if workflow:
        workflow_lower = workflow.lower()
        return (
            f"actions/workflows/{workflow_lower}/badge.svg" in visible
            or f"github/actions/workflow/status/{repo.full_name.lower()}/{workflow_lower}" in visible
        )
    if kind == "arduino_library_manager":
        return "ardu-badge.com/badge/" in visible
    if kind == "release":
        return f"github/release/{repo.full_name}".lower() in visible or "releases/latest" in visible
    if kind == "tag":
        return f"github/tag/{repo.full_name}".lower() in visible or "/tags" in visible
    if kind == "license":
        return (
            f"github/license/{repo.full_name}".lower() in visible
            or "img.shields.io/badge/license" in visible
            or "license:" in visible
        )
    if kind == "stars":
        return f"github/stars/{repo.full_name}".lower() in visible or "/stargazers" in visible
    if kind == "forks":
        return f"github/forks/{repo.full_name}".lower() in visible or "/network/members" in visible
    return False


def remove_skipped_badges(markdown: str, repo: Repository) -> str:
    """Remove generated badge lines that have been marked inapplicable."""
    workflow_skips = skipped_workflow_badges(repo)
    kind_skips = skipped_badge_kinds(repo)
    kept = []
    changed = False
    for line in markdown.splitlines():
        lower = line.lower()
        remove = any(f"actions/workflows/{workflow}/badge.svg" in lower for workflow in workflow_skips)
        remove = remove or ("arduino_library_manager" in kind_skips and "ardu-badge.com/badge/" in lower)
        remove = remove or ("release" in kind_skips and f"github/release/{repo.full_name}".lower() in lower)
        remove = remove or ("tag" in kind_skips and f"github/tag/{repo.full_name}".lower() in lower)
        remove = remove or ("license" in kind_skips and f"github/license/{repo.full_name}".lower() in lower)
        remove = remove or ("stars" in kind_skips and f"github/stars/{repo.full_name}".lower() in lower)
        remove = remove or ("forks" in kind_skips and f"github/forks/{repo.full_name}".lower() in lower)
        if remove:
            changed = True
            continue
        kept.append(line)
    if not changed:
        return markdown
    return "\n".join(kept).rstrip() + "\n"


def workflow_label(workflow: str) -> str:
    """Convert a workflow filename into a badge label."""
    words = workflow.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").split()
    return " ".join("CI" if word.lower() == "ci" else word.title() for word in words)


def badge_lines(
    repo: Repository,
    repo_dir: Path,
    workflows: list[str],
    readme: str,
    *,
    target_path: str = "",
    target_paths: list[str] | None = None,
) -> list[str]:
    """Build visible README badge lines that are not already present."""
    lines = []
    root_library = root_library_name(repo_dir)
    release = latest_release(repo)
    tag = latest_tag(repo)
    scoped_file_paths = target_paths if target_paths is not None else all_paths(repo_dir)

    def add(line: str, *, kind: str | None = None, workflow: str | None = None) -> None:
        if workflow and workflow.lower() in skipped_workflow_badges(repo):
            return
        if workflow and target_path and not workflow_mentions_path(repo_dir, workflow, target_path):
            if workflow != "jsoncheck.yml" or not has_json_files(scoped_file_paths):
                return
        if kind and kind.lower() in skipped_badge_kinds(repo):
            return
        if not has_badge_kind(readme, repo, kind=kind, workflow=workflow):
            lines.append(line)

    if "arduino_test_runner.yml" in workflows:
        add(
            f"[![Arduino CI](https://github.com/{repo.full_name}/actions/workflows/arduino_test_runner.yml/badge.svg)]"
            f"(https://github.com/{repo.full_name}/actions/workflows/arduino_test_runner.yml)",
            workflow="arduino_test_runner.yml",
        )
    if "arduino-lint.yml" in workflows:
        add(
            f"[![Arduino-lint](https://github.com/{repo.full_name}/actions/workflows/arduino-lint.yml/badge.svg)]"
            f"(https://github.com/{repo.full_name}/actions/workflows/arduino-lint.yml)",
            workflow="arduino-lint.yml",
        )
    if "jsoncheck.yml" in workflows:
        add(
            f"[![JSON check](https://github.com/{repo.full_name}/actions/workflows/jsoncheck.yml/badge.svg)]"
            f"(https://github.com/{repo.full_name}/actions/workflows/jsoncheck.yml)",
            workflow="jsoncheck.yml",
        )
    if "ant.yml" in workflows:
        add(
            f"[![Java CI](https://github.com/{repo.full_name}/actions/workflows/ant.yml/badge.svg)]"
            f"(https://github.com/{repo.full_name}/actions/workflows/ant.yml)",
            workflow="ant.yml",
        )

    specific = {"arduino_test_runner.yml", "arduino-lint.yml", "jsoncheck.yml", "ant.yml"}
    for workflow in workflows:
        if workflow in specific:
            continue
        if workflow.lower() in skipped_workflow_badges(repo):
            continue
        if target_path and not workflow_mentions_path(repo_dir, workflow, target_path):
            continue
        label = workflow_label(workflow)
        add(
            f"[![{label}](https://github.com/{repo.full_name}/actions/workflows/{workflow}/badge.svg)]"
            f"(https://github.com/{repo.full_name}/actions/workflows/{workflow})",
            workflow=workflow,
        )

    if target_path:
        return lines

    if root_library and arduino_badge_available(root_library):
        encoded = quote(root_library)
        add(
            f"[![Arduino Library Manager](https://www.ardu-badge.com/badge/{encoded}.svg)]"
            f"(https://www.ardu-badge.com/{encoded})",
            kind="arduino_library_manager",
        )
    if release:
        add(
            f"[![GitHub release](https://flat.badgen.net/github/release/{repo.full_name})]"
            f"(https://github.com/{repo.full_name}/releases/latest)",
            kind="release",
        )
    elif tag:
        add(
            f"[![GitHub tag](https://flat.badgen.net/github/tag/{repo.full_name})]"
            f"(https://github.com/{repo.full_name}/tags)",
            kind="tag",
        )
    if has_license(repo, repo_dir):
        license_file = license_path(repo_dir)
        add(
            f"[![License](https://flat.badgen.net/github/license/{repo.full_name})]"
            f"(https://github.com/{repo.full_name}/blob/{repo.default_branch}/{license_file})",
            kind="license",
        )
    if not repo.private:
        add(
            f"[![Stars](https://flat.badgen.net/github/stars/{repo.full_name})]"
            f"(https://github.com/{repo.full_name}/stargazers)",
            kind="stars",
        )
        add(
            f"[![Forks](https://flat.badgen.net/github/forks/{repo.full_name})]"
            f"(https://github.com/{repo.full_name}/network/members)",
            kind="forks",
        )
    return lines


def line_is_badge(line: str) -> bool:
    """Return whether a line is an existing badge line."""
    lower = line.lower().strip()
    if not lower:
        return True
    return (
        ("<img" in lower and ("badge" in lower or "shields.io" in lower or "badgen.net" in lower))
        or (
            "![" in lower
            and (
                "badge.svg" in lower
                or "shields.io" in lower
                or "badgen.net" in lower
                or "ardu-badge.com" in lower
                or "codecov.io" in lower
            )
        )
    )


def badge_insertion_index(lines: list[str]) -> int:
    """Return where generated badges should be inserted."""
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and lines[index].strip() == "---":
        closing = None
        for probe in range(index + 1, min(len(lines), index + 20)):
            if lines[probe].strip() == "---":
                closing = probe
                break
        if closing is not None and any(":" in line for line in lines[index + 1 : closing]):
            index = closing + 1
        else:
            index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
    if index < len(lines) and lines[index].startswith("# "):
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        while index < len(lines) and line_is_badge(lines[index]):
            index += 1
        return index
    while index < len(lines) and line_is_badge(lines[index]):
        index += 1
    return index


def folder_readme_body(repo: Repository, target_path: str, target_paths: list[str]) -> str:
    """Return a deterministic README body for a repository subfolder."""
    body = [
        f"This folder is part of [{repo.full_name}](https://github.com/{repo.full_name}).",
    ]
    visible_files = [
        path
        for path in target_paths
        if path != "README.md" and not path.endswith("/") and "__pycache__" not in path.split("/")
    ]
    if visible_files:
        body.extend(["", "## Contents", ""])
        body.extend(f"- `{path}`" for path in sorted(visible_files))
    return "\n".join(body)


def insert_badges(
    markdown: str,
    additions: list[str],
    repo: Repository,
    *,
    title: str | None = None,
    body: str | None = None,
) -> str:
    """Insert badge lines without disturbing existing visible badges."""
    if not markdown.strip():
        readme_title = title or repo.name
        readme_body = body or repo.description.strip() or f"Repository for {repo.name}."
        badge_block = "\n".join(additions)
        if badge_block:
            return f"# {readme_title}\n\n{badge_block}\n\n{readme_body}\n"
        return f"# {readme_title}\n\n{readme_body}\n"
    if not additions:
        return markdown
    lines = markdown.splitlines()
    index = badge_insertion_index(lines)
    block = additions + [""]
    if index == 0:
        return "\n".join(block + lines).rstrip() + "\n"
    return "\n".join(lines[:index] + block + lines[index:]).rstrip() + "\n"


def json_workflow() -> str:
    """Return the JSON check workflow."""
    return """name: JSON check

on:
  push:
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v6
      - name: json-syntax-check
        uses: limitusus/json-syntax-check@v2
        with:
          pattern: "\\\\.json$"
"""


def arduino_lint_workflow() -> str:
    """Return a conservative Arduino lint workflow."""
    return """name: Arduino-lint

on:
  push:
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  lint:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v6
      - uses: arduino/arduino-lint-action@v2
        with:
          project-type: all
          recursive: true
          compliance: permissive
          library-manager: false
"""


def arduino_compile_workflow(repo: Repository) -> str:
    """Return a conservative Arduino compile workflow for root libraries."""
    fqbn = "arduino:avr:uno"
    platforms = ""
    if repo.name == "ESP32Emic2":
        fqbn = "esp32:esp32:esp32"
        platforms = """          platforms: |
            - name: esp32:esp32
              source-url: https://dl.espressif.com/dl/package_esp32_index.json
"""
    return f"""name: Arduino CI

on:
  push:
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  compile:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v6
      - name: Compile examples
        uses: arduino/compile-sketches@v1
        with:
          fqbn: {fqbn}
{platforms}          libraries: |
            - source-path: ./
          sketch-paths: |
            examples
"""


def python_folder_workflow(target_path: str) -> str:
    """Return a scoped Python validation workflow for a repository folder."""
    title = display_title(target_path)
    workflow = python_workflow_name(target_path)
    return f"""name: {title} Python

on:
  push:
    paths:
      - "{target_path}/**"
      - ".github/workflows/{workflow}"
  pull_request:
    paths:
      - "{target_path}/**"
      - ".github/workflows/{workflow}"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  python:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v5
        with:
          python-version: "3.x"
      - name: Compile Python scripts
        run: |
          python - <<'PY'
          from pathlib import Path
          import py_compile

          for path in sorted(Path("{target_path}").rglob("*.py")):
              py_compile.compile(str(path), doraise=True)
          PY
      - name: Install lint tooling
        run: python -m pip install --upgrade ruff
      - name: Lint Python scripts
        run: ruff check "{target_path}"
"""


def generated_workflow_template(repo: Repository, workflow: str) -> str | None:
    """Return a generated workflow template for safe removal checks."""
    if workflow == "jsoncheck.yml":
        return json_workflow()
    if workflow == "arduino-lint.yml":
        return arduino_lint_workflow()
    if workflow == "arduino_test_runner.yml":
        return arduino_compile_workflow(repo)
    return None


def generated_workflow_templates(repo: Repository, workflow: str) -> list[str]:
    """Return current and legacy generated workflow templates."""
    template = generated_workflow_template(repo, workflow)
    if template is None:
        return []
    legacy = template.replace("actions/checkout@v6", "actions/checkout@v4")
    if legacy == template:
        return [template]
    return [template, legacy]


def remove_skipped_generated_workflows(repo: Repository, repo_dir: Path) -> list[str]:
    """Remove generated workflows that are marked inapplicable."""
    removed = []
    for workflow in repo.skip_workflows:
        path = repo_dir / ".github" / "workflows" / workflow
        templates = generated_workflow_templates(repo, workflow)
        if not path.exists() or not templates:
            continue
        if read_text(path).strip() not in {template.strip() for template in templates}:
            continue
        path.unlink()
        removed.append(workflow)
    return removed


def refresh_generated_workflows(repo: Repository, repo_dir: Path) -> list[str]:
    """Update generated workflows that still match a known legacy template."""
    refreshed = []
    for workflow in workflow_files(repo_dir):
        path = repo_dir / ".github" / "workflows" / workflow
        current = generated_workflow_template(repo, workflow)
        templates = generated_workflow_templates(repo, workflow)
        if current is None or not templates:
            continue
        existing = read_text(path).strip()
        if existing == current.strip():
            continue
        legacy_templates = {template.strip() for template in templates[1:]}
        if existing not in legacy_templates:
            continue
        if write_if_changed(path, current):
            refreshed.append(workflow)
    return refreshed


def apply_repository(
    repo: Repository,
    *,
    apply: bool,
    keep_worktrees: bool,
    target_path: str = "",
) -> PlannedChange:
    """Plan or apply maintenance changes to one repository."""
    target = WORK_ROOT / repo.name
    clone_repository(repo, target)
    paths = all_paths(target)
    target_paths = scoped_paths(paths, target_path)
    workflows = workflow_files(target)
    changes = []
    skipped = skipped_workflow_names(repo)

    removed = remove_skipped_generated_workflows(repo, target)
    if removed:
        changes.append("remove inapplicable generated workflow(s): " + ", ".join(removed))
        workflows = workflow_files(target)

    refreshed = refresh_generated_workflows(repo, target)
    if refreshed:
        changes.append("refresh generated workflow(s): " + ", ".join(refreshed))
        workflows = workflow_files(target)

    if target_path:
        python_workflow = python_workflow_name(target_path)
        if (
            has_python_files(target_paths)
            and python_workflow not in workflows
            and python_workflow not in skipped
        ):
            if write_if_changed(
                target / ".github/workflows" / python_workflow,
                python_folder_workflow(target_path),
            ):
                changes.append(f"add Python workflow for {target_path}")
                workflows = workflow_files(target)
        if has_json_files(target_paths) and "jsoncheck.yml" not in workflows and "jsoncheck.yml" not in skipped:
            if write_if_changed(target / ".github/workflows/jsoncheck.yml", json_workflow()):
                changes.append("add JSON check workflow")
                workflows = workflow_files(target)
    else:
        sketches = sketch_paths(paths)
        if sketches and "arduino-lint.yml" not in workflows and "arduino-lint.yml" not in skipped:
            if write_if_changed(target / ".github/workflows/arduino-lint.yml", arduino_lint_workflow()):
                changes.append("add Arduino-lint workflow")
                workflows = workflow_files(target)

        existing_build = any(name.lower() in {"ci.yml", "ci.yaml", "build.yml", "build.yaml"} for name in workflows)
        root_library = (target / "library.properties").exists()
        if (
            root_library
            and "arduino_test_runner.yml" not in workflows
            and "arduino_test_runner.yml" not in skipped
            and not existing_build
            and (target / "examples").exists()
        ):
            if write_if_changed(
                target / ".github/workflows/arduino_test_runner.yml",
                arduino_compile_workflow(repo),
            ):
                changes.append("add Arduino CI workflow")
                workflows = workflow_files(target)

        if has_json_files(paths) and "jsoncheck.yml" not in workflows and "jsoncheck.yml" not in skipped:
            if write_if_changed(target / ".github/workflows/jsoncheck.yml", json_workflow()):
                changes.append("add JSON check workflow")
                workflows = workflow_files(target)

    if target_path:
        readme_path = target / target_path / "README.md"
    else:
        readme_path = root_readme(target)
    readme = remove_skipped_badges(read_text(readme_path), repo)
    additions = badge_lines(
        repo,
        target,
        workflows,
        readme,
        target_path=target_path,
        target_paths=target_paths,
    )
    title = display_title(target_path) if target_path else None
    body = folder_readme_body(repo, target_path, target_paths) if target_path else None
    if write_if_changed(readme_path, insert_badges(readme, additions, repo, title=title, body=body)):
        if target_path:
            changes.append(f"create or update {target_path}/README.md")
        else:
            changes.append("create README" if not readme else "update README badges")

    status = run(["git", "status", "--short"], cwd=target).stdout.strip()
    planned = PlannedChange(repository=repo, changes=changes, status=status)
    if not status:
        if not keep_worktrees:
            shutil.rmtree(target)
        return planned
    if not apply:
        if not keep_worktrees:
            shutil.rmtree(target)
        return planned

    run(["git", "add", "."], cwd=target)
    run(["git", "commit", "-m", "Add repository badges and CI checks"], cwd=target)
    run(["git", "push", "origin", f"HEAD:{repo.default_branch}"], cwd=target, timeout=300)
    if not keep_worktrees:
        shutil.rmtree(target)
    return planned


def write_plan_report(changes: list[PlannedChange], output_dir: Path) -> None:
    """Write Markdown and CSV reports for planned/applied changes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "repo-maintenance-plan.csv"
    md_path = output_dir / "repo-maintenance-plan.md"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["repository", "changes", "status"])
        for change in changes:
            writer.writerow([change.repository.full_name, "; ".join(change.changes), change.status])
    with md_path.open("w", encoding="utf-8") as handle:
        handle.write("# Repository Maintenance Plan\n\n")
        handle.write("| Repository | Changes | Status |\n")
        handle.write("|---|---|---|\n")
        for change in changes:
            status = "<br>".join(change.status.splitlines()) if change.status else "-"
            handle.write(
                f"| [{change.repository.full_name}]({change.repository.url}) | "
                f"{'; '.join(change.changes) or '-'} | {status} |\n"
            )


def command_plan_or_apply(args: argparse.Namespace, *, apply: bool) -> None:
    """Run plan/apply mode."""
    repositories = selected_repositories(args)
    if WORK_ROOT.exists() and not args.keep_worktrees:
        shutil.rmtree(WORK_ROOT)
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    changes = []
    for repo in repositories:
        print(f"== {repo.full_name} ==", flush=True)
        change = apply_repository(
            repo,
            apply=apply,
            keep_worktrees=args.keep_worktrees,
            target_path=args.path,
        )
        if change.status:
            print(f"{repo.full_name}: {', '.join(change.changes)}")
            print(change.status)
        changes.append(change)
    write_plan_report(changes, args.output_dir)
    if WORK_ROOT.exists() and not args.keep_worktrees:
        shutil.rmtree(WORK_ROOT)


def latest_workflow_run(repo: Repository, workflow: str) -> dict[str, Any] | None:
    """Return the latest run for a workflow."""
    result = run(
        [
            "gh",
            "run",
            "list",
            "--repo",
            repo.full_name,
            "--workflow",
            workflow,
            "--limit",
            "1",
            "--json",
            "databaseId,status,conclusion,headBranch,headSha,createdAt,url",
        ],
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    runs = json.loads(result.stdout or "[]")
    return runs[0] if runs else None


def decode_gh_json(stdout: str) -> Any:
    """Decode GitHub CLI JSON, including line-separated paginated JSON values."""
    text = stdout.strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        values = []
        for line in text.splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, list):
                    values.extend(value)
                else:
                    values.append(value)
        return values


def gh_api_paginated(path: str) -> tuple[bool, Any, str]:
    """Call a GitHub API endpoint, returning success, JSON, and error text."""
    result = run(["gh", "api", path, "--paginate"], check=False, timeout=60)
    if result.returncode != 0:
        return False, [], result.stderr.strip()
    return True, decode_gh_json(result.stdout), ""


def normalize_dependabot_alert(repo: Repository, alert: dict[str, Any]) -> SecurityAlertRow:
    """Convert a Dependabot alert to a report row."""
    advisory = alert.get("security_advisory") or {}
    vulnerability = alert.get("security_vulnerability") or {}
    dependency = alert.get("dependency") or {}
    package = dependency.get("package") or {}
    patched = vulnerability.get("first_patched_version") or {}
    package_name = package.get("name") or dependency.get("package_name") or ""
    package_ecosystem = package.get("ecosystem") or vulnerability.get("package", {}).get("ecosystem") or ""
    manifest = dependency.get("manifest_path") or ""
    fixed_in = patched.get("identifier") or ""
    action = "Review and update the vulnerable dependency."
    if package_name and fixed_in:
        action = f"Update {package_name} to {fixed_in} or newer."
    elif package_name:
        action = f"Review and update {package_name}; no patched version was reported."
    location = " ".join(item for item in [package_ecosystem, package_name, manifest] if item)
    return SecurityAlertRow(
        repository=repo,
        scanner="Dependabot",
        state=alert.get("state") or "open",
        severity=advisory.get("severity") or vulnerability.get("severity") or "",
        title=advisory.get("summary") or advisory.get("ghsa_id") or "Dependabot alert",
        location=location,
        url=alert.get("html_url") or "",
        action=action,
    )


def normalize_code_scanning_alert(repo: Repository, alert: dict[str, Any]) -> SecurityAlertRow:
    """Convert a code-scanning alert to a report row."""
    rule = alert.get("rule") or {}
    instance = alert.get("most_recent_instance") or {}
    location_data = instance.get("location") or {}
    location = location_data.get("path") or ""
    line = location_data.get("start_line")
    if location and line:
        location = f"{location}:{line}"
    return SecurityAlertRow(
        repository=repo,
        scanner="Code scanning",
        state=alert.get("state") or "open",
        severity=rule.get("security_severity_level") or rule.get("severity") or "",
        title=rule.get("description") or rule.get("name") or rule.get("id") or "Code scanning alert",
        location=location,
        url=alert.get("html_url") or "",
        action="Review the alert and patch the affected source or workflow configuration.",
    )


def normalize_secret_scanning_alert(repo: Repository, alert: dict[str, Any]) -> SecurityAlertRow:
    """Convert a secret-scanning alert to a report row."""
    return SecurityAlertRow(
        repository=repo,
        scanner="Secret scanning",
        state=alert.get("state") or "open",
        severity="secret",
        title=alert.get("secret_type_display_name") or alert.get("secret_type") or "Secret scanning alert",
        location=alert.get("resolution_comment") or "",
        url=alert.get("html_url") or "",
        action="Revoke or rotate the secret, then remove or invalidate exposed copies.",
    )


SECURITY_ENDPOINTS = {
    "Dependabot": (
        "dependabot/alerts?state=open&per_page=100",
        normalize_dependabot_alert,
    ),
    "Code scanning": (
        "code-scanning/alerts?state=open&per_page=100",
        normalize_code_scanning_alert,
    ),
    "Secret scanning": (
        "secret-scanning/alerts?state=open&per_page=100",
        normalize_secret_scanning_alert,
    ),
}


def security_alert_rows(repo: Repository) -> list[SecurityAlertRow]:
    """Return normalized security-alert rows for one repository."""
    rows = []
    for scanner, (endpoint, normalizer) in SECURITY_ENDPOINTS.items():
        ok, payload, error = gh_api_paginated(f"repos/{repo.full_name}/{endpoint}")
        if not ok:
            rows.append(
                SecurityAlertRow(
                    repository=repo,
                    scanner=scanner,
                    state="unavailable",
                    title=error,
                    action="Enable this scanner or grant token access if this report should include it.",
                )
            )
            continue
        alerts = payload if isinstance(payload, list) else [payload]
        if not alerts:
            rows.append(
                SecurityAlertRow(
                    repository=repo,
                    scanner=scanner,
                    state="no open alerts",
                    action="No action required.",
                )
            )
            continue
        rows.extend(normalizer(repo, alert) for alert in alerts)
    return rows


def command_security_alerts(args: argparse.Namespace) -> None:
    """Write a report of open GitHub security alerts."""
    repositories = selected_repositories(args)
    rows = []
    for repo in repositories:
        rows.extend(security_alert_rows(repo))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "repo-maintenance-security.csv"
    md_path = args.output_dir / "repo-maintenance-security.md"
    headers = ["repository", "scanner", "state", "severity", "title", "location", "url", "action"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(
                [
                    row.repository.full_name,
                    row.scanner,
                    row.state,
                    row.severity,
                    row.title,
                    row.location,
                    row.url,
                    row.action,
                ]
            )
    with md_path.open("w", encoding="utf-8") as handle:
        handle.write("# Repository Security Alerts\n\n")
        handle.write("| Repository | Scanner | State | Severity | Title | Location | Action |\n")
        handle.write("|---|---|---|---|---|---|---|\n")
        for row in rows:
            handle.write(
                f"| [{row.repository.full_name}]({row.repository.url}) | {row.scanner} | "
                f"{row.state} | {row.severity or '-'} | {row.title or '-'} | "
                f"{row.location or '-'} | {row.action or '-'} |\n"
            )


def command_verify_actions(args: argparse.Namespace) -> None:
    """Write a report of latest workflow runs for manifest repositories."""
    repositories = selected_repositories(args)
    rows = []
    for repo in repositories:
        clone_target = WORK_ROOT / repo.name
        clone_repository(repo, clone_target)
        workflows = workflow_files(clone_target)
        shutil.rmtree(clone_target)
        for workflow in workflows:
            run_data = latest_workflow_run(repo, workflow)
            rows.append((repo, workflow, run_data))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "repo-maintenance-actions.csv"
    md_path = args.output_dir / "repo-maintenance-actions.md"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["repository", "workflow", "status", "conclusion", "url"])
        for repo, workflow, run_data in rows:
            writer.writerow(
                [
                    repo.full_name,
                    workflow,
                    (run_data or {}).get("status", "no runs"),
                    (run_data or {}).get("conclusion", ""),
                    (run_data or {}).get("url", ""),
                ]
            )
    with md_path.open("w", encoding="utf-8") as handle:
        handle.write("# Repository Actions Verification\n\n")
        handle.write("| Repository | Workflow | Status | Conclusion | Run |\n")
        handle.write("|---|---|---|---|---|\n")
        for repo, workflow, run_data in rows:
            status = (run_data or {}).get("status", "no runs")
            conclusion = (run_data or {}).get("conclusion", "")
            url = (run_data or {}).get("url", "")
            link = f"[run]({url})" if url else "-"
            handle.write(f"| [{repo.full_name}]({repo.url}) | {workflow} | {status} | {conclusion} | {link} |\n")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="show deterministic changes without pushing")
    plan_parser.add_argument("--repo", action="append", help="single repository NAME or OWNER/NAME")
    plan_parser.add_argument("--repositories", nargs="*", help="optional NAME or OWNER/NAME filters")
    plan_parser.add_argument("--path", type=target_path_arg, default="", help="repository-relative folder target")
    plan_parser.add_argument("--keep-worktrees", action="store_true")

    apply_parser = subparsers.add_parser("apply", help="apply and push deterministic changes")
    apply_parser.add_argument("--repo", action="append", help="single repository NAME or OWNER/NAME")
    apply_parser.add_argument("--repositories", nargs="*", help="optional NAME or OWNER/NAME filters")
    apply_parser.add_argument("--path", type=target_path_arg, default="", help="repository-relative folder target")
    apply_parser.add_argument("--keep-worktrees", action="store_true")

    verify_parser = subparsers.add_parser("verify-actions", help="report latest workflow runs")
    verify_parser.add_argument("--repo", action="append", help="single repository NAME or OWNER/NAME")
    verify_parser.add_argument("--repositories", nargs="*", help="optional NAME or OWNER/NAME filters")
    verify_parser.set_defaults(keep_worktrees=False)

    security_parser = subparsers.add_parser("security-alerts", help="report GitHub security alerts")
    security_parser.add_argument("--repo", action="append", help="single repository NAME or OWNER/NAME")
    security_parser.add_argument("--repositories", nargs="*", help="optional NAME or OWNER/NAME filters")
    security_parser.set_defaults(keep_worktrees=False)
    return parser


def main() -> None:
    """Run the command-line interface."""
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "plan":
        command_plan_or_apply(args, apply=False)
    elif args.command == "apply":
        command_plan_or_apply(args, apply=True)
    elif args.command == "verify-actions":
        command_verify_actions(args)
    elif args.command == "security-alerts":
        command_security_alerts(args)


if __name__ == "__main__":
    main()
