#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html import escape


OWNER = os.environ.get("GITHUB_OWNER", "ripred")
TOKEN = os.environ.get("GITHUB_TOKEN")
README_PATH = "README.md"
LANGUAGE_SVG_PATH = "assets/top-languages.svg"
START = "<!-- RECENT-PUBLIC-WORK:START -->"
END = "<!-- RECENT-PUBLIC-WORK:END -->"
EXCLUDED_REPOS = {".github", "ripred"}
OWNED_PUBLIC_FORKS = {"Gately"}
MAX_REPOS = 6
MAX_DESCRIPTION_LENGTH = 140
MAX_LANGUAGES = 6
LANGUAGE_COLORS = {
    "C": "#555555",
    "C++": "#f34b7d",
    "CMake": "#da3434",
    "CSS": "#663399",
    "HTML": "#e34c26",
    "Java": "#b07219",
    "JavaScript": "#f1e05a",
    "Processing": "#0096d8",
    "Python": "#3572a5",
    "Rust": "#dea584",
    "Shell": "#89e051",
    "TypeScript": "#3178c6",
}


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


def format_description(value):
    description = "Public repository." if not value else " ".join(value.split())
    if len(description) > MAX_DESCRIPTION_LENGTH:
        description = description[: MAX_DESCRIPTION_LENGTH - 1].rstrip() + "..."
    if description[-1] not in ".!?":
        description += "."
    return description


def public_profile_repositories(repos):
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
    return safe_repos


def render_recent_work(repos):
    safe_repos = public_profile_repositories(repos)

    safe_repos.sort(key=lambda repo: parse_github_time(repo["pushed_at"]), reverse=True)
    lines = []
    for repo in safe_repos[:MAX_REPOS]:
        name = repo["name"]
        url = repo["html_url"]
        description = format_description(repo.get("description"))
        lines.append(f"- [{name}]({url}) - {description}")

    if not lines:
        lines.append("- No recent public repository activity found.")

    return "\n".join(lines)


def fetch_language_totals(repos):
    totals = {}
    for repo in repos:
        name = repo["name"]
        try:
            languages = github_get(f"/repos/{OWNER}/{name}/languages")
        except (urllib.error.URLError, ValueError, KeyError) as error:
            print(f"Skipping language stats for {name}: {error}", file=sys.stderr)
            continue

        for language, byte_count in languages.items():
            totals[language] = totals.get(language, 0) + int(byte_count)
    return totals


def format_percent(value):
    text = f"{value:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return f"{text}%"


def render_language_svg(language_totals):
    top_languages = sorted(language_totals.items(), key=lambda item: item[1], reverse=True)[:MAX_LANGUAGES]
    width = 520
    row_height = 28
    height = 84 + max(1, len(top_languages)) * row_height
    total = sum(language_totals.values())

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" role="img" aria-labelledby="title desc" viewBox="0 0 {width} {height}">',
        '<title id="title">Top public repository languages</title>',
        '<desc id="desc">Language breakdown for public profile repositories.</desc>',
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="6" fill="#ffffff" stroke="#d0d7de"/>',
        '<text x="20" y="30" fill="#24292f" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="16" font-weight="600">Top Public Repo Languages</text>',
        '<text x="20" y="50" fill="#57606a" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="12">Owned public repositories plus maintained public forks</text>',
    ]

    if not top_languages or total == 0:
        parts.extend(
            [
                '<text x="20" y="86" fill="#57606a" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="13">No language data found.</text>',
                "</svg>",
            ]
        )
        return "\n".join(parts)

    chart_x = 20
    chart_y = 66
    chart_width = width - 40
    offset = 0.0
    parts.append(f'<rect x="{chart_x}" y="{chart_y}" width="{chart_width}" height="10" rx="5" fill="#eaeef2"/>')
    for language, byte_count in top_languages:
        segment_width = chart_width * byte_count / total
        color = LANGUAGE_COLORS.get(language, "#6e7781")
        parts.append(
            f'<rect x="{chart_x + offset:.2f}" y="{chart_y}" width="{segment_width:.2f}" height="10" fill="{color}"/>'
        )
        offset += segment_width

    for index, (language, byte_count) in enumerate(top_languages):
        percent = format_percent(byte_count * 100 / total)
        y = 98 + index * row_height
        color = LANGUAGE_COLORS.get(language, "#6e7781")
        parts.extend(
            [
                f'<circle cx="26" cy="{y - 5}" r="5" fill="{color}"/>',
                f'<text x="40" y="{y}" fill="#24292f" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="13">{escape(language)}</text>',
                f'<text x="{width - 20}" y="{y}" fill="#57606a" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="13" text-anchor="end">{percent}</text>',
            ]
        )

    parts.append("</svg>")
    return "\n".join(parts)


def write_language_svg(repos):
    safe_repos = public_profile_repositories(repos)
    language_totals = fetch_language_totals(safe_repos)
    svg = render_language_svg(language_totals)
    os.makedirs(os.path.dirname(LANGUAGE_SVG_PATH), exist_ok=True)

    current = None
    if os.path.exists(LANGUAGE_SVG_PATH):
        with open(LANGUAGE_SVG_PATH, "r", encoding="utf-8") as language_svg:
            current = language_svg.read()

    if svg != current:
        with open(LANGUAGE_SVG_PATH, "w", encoding="utf-8") as language_svg:
            language_svg.write(svg)
            language_svg.write("\n")
        return True
    return False


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
        readme_changed = update_readme(block)
        languages_changed = write_language_svg(repos)
    except (urllib.error.URLError, RuntimeError, KeyError, ValueError) as error:
        print(f"Failed to update profile content: {error}", file=sys.stderr)
        return 1

    if readme_changed or languages_changed:
        print("Profile content updated.")
    else:
        print("Profile content already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
