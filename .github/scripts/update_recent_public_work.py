#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


OWNER = os.environ.get("GITHUB_OWNER", "ripred")
TOKEN = os.environ.get("GITHUB_TOKEN")
README_PATH = "README.md"
START = "<!-- RECENT-PUBLIC-WORK:START -->"
END = "<!-- RECENT-PUBLIC-WORK:END -->"
EXCLUDED_REPOS = {".github", "ripred"}
OWNED_PUBLIC_FORKS = {"Gately"}
MAX_REPOS = 6
MAX_DESCRIPTION_LENGTH = 140


def github_get(path, query=None):
    if query:
        path = f"{path}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(f"https://api.github.com{path}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if TOKEN:
        request.add_header("Authorization", f"Bearer {TOKEN}")

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def public_repositories():
    repos = []
    page = 1
    while True:
        batch = github_get(
            f"/users/{OWNER}/repos",
            {
                "type": "owner",
                "sort": "pushed",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
        )
        if not batch:
            break
        repos.extend(batch)
        page += 1
    return repos


def parse_github_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_date(value):
    return parse_github_time(value).strftime("%Y-%m-%d")


def format_description(value):
    description = "Public repository." if not value else " ".join(value.split())
    if len(description) > MAX_DESCRIPTION_LENGTH:
        description = description[: MAX_DESCRIPTION_LENGTH - 1].rstrip() + "..."
    if description[-1] not in ".!?":
        description += "."
    return description


def render_recent_work(repos):
    safe_repos = []
    for repo in repos:
        name = repo.get("name", "")
        if name in EXCLUDED_REPOS:
            continue
        if repo.get("private") is not False:
            continue
        if repo.get("fork") is True and name not in OWNED_PUBLIC_FORKS:
            continue
        if repo.get("archived") is True:
            continue
        if repo.get("visibility") != "public":
            continue
        safe_repos.append(repo)

    safe_repos.sort(key=lambda repo: parse_github_time(repo["pushed_at"]), reverse=True)
    lines = []
    for repo in safe_repos[:MAX_REPOS]:
        name = repo["name"]
        url = repo["html_url"]
        description = format_description(repo.get("description"))
        pushed = format_date(repo["pushed_at"])
        lines.append(f"- [{name}]({url}) - {description} Updated {pushed}.")

    if not lines:
        lines.append("- No recent public repository activity found.")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines.append(f"")
    lines.append(f"_Updated automatically from public GitHub repository metadata on {generated_at}._")
    return "\n".join(lines)


def update_readme(block):
    with open(README_PATH, "r", encoding="utf-8") as readme:
        content = readme.read()
    if START not in content or END not in content:
        raise RuntimeError("Recent public work markers are missing from README.md")

    before, rest = content.split(START, 1)
    _, after = rest.split(END, 1)
    updated = f"{before}{START}\n{block}\n{END}{after}"
    if updated != content:
        with open(README_PATH, "w", encoding="utf-8") as readme:
            readme.write(updated)
        return True
    return False


def main():
    try:
        repos = public_repositories()
        block = render_recent_work(repos)
        changed = update_readme(block)
    except (urllib.error.URLError, RuntimeError, KeyError, ValueError) as error:
        print(f"Failed to update recent public work: {error}", file=sys.stderr)
        return 1

    print("README.md updated." if changed else "README.md already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
