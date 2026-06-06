#!/usr/bin/env python3
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html import escape


OWNER = os.environ.get("GITHUB_OWNER", "ripred")
TOKEN = os.environ.get("GITHUB_TOKEN")
README_PATH = "README.md"
LANGUAGE_SVG_PATH = "assets/top-languages.svg"
LANGUAGE_LOC_CACHE_PATH = "assets/language-loc-cache.json"
START = "<!-- RECENT-PUBLIC-WORK:START -->"
END = "<!-- RECENT-PUBLIC-WORK:END -->"
EXCLUDED_REPOS = {".github", "ripred"}
OWNED_PUBLIC_FORKS = {"Gately"}
MAX_REPOS = 6
MAX_DESCRIPTION_LENGTH = 140
MAX_LANGUAGES = 6
MAX_LOC_FILE_BYTES = 1_000_000
FORCE_LANGUAGE_LOC = os.environ.get("FORCE_LANGUAGE_LOC", "").lower() in {"1", "true", "yes", "on"}
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
LOC_LANGUAGE_EXTENSIONS = {
    ".c": "C",
    ".cc": "C++",
    ".cjs": "JavaScript",
    ".cmake": "CMake",
    ".cpp": "C++",
    ".css": "CSS",
    ".cxx": "C++",
    ".h": "C++",
    ".hh": "C++",
    ".hpp": "C++",
    ".htm": "HTML",
    ".html": "HTML",
    ".hxx": "C++",
    ".ino": "C++",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".pde": "Processing",
    ".py": "Python",
    ".rs": "Rust",
    ".scss": "SCSS",
    ".sh": "Shell",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
}
LOC_LANGUAGE_FILENAMES = {
    "CMakeLists.txt": "CMake",
}
SKIPPED_LOC_PATH_PARTS = {
    ".arduino_ci",
    ".git",
    ".github",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
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


def read_json_file(path, fallback):
    if not os.path.exists(path):
        return fallback
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def write_json_file(path, value):
    serialized = json.dumps(value, indent=2, sort_keys=True)
    serialized += "\n"

    current = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as file:
            current = file.read()

    if serialized != current:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            file.write(serialized)
        return True
    return False


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


def fetch_language_data(repos):
    totals = {}
    by_repo = {}
    for repo in repos:
        name = repo["name"]
        try:
            languages = github_get(f"/repos/{OWNER}/{name}/languages")
        except (urllib.error.URLError, ValueError, KeyError) as error:
            print(f"Skipping language stats for {name}: {error}", file=sys.stderr)
            continue

        by_repo[name] = languages
        for language, byte_count in languages.items():
            totals[language] = totals.get(language, 0) + int(byte_count)
    return totals, by_repo


def loc_language_for_path(path):
    name = os.path.basename(path)
    if name in LOC_LANGUAGE_FILENAMES:
        return LOC_LANGUAGE_FILENAMES[name]

    _, extension = os.path.splitext(name)
    return LOC_LANGUAGE_EXTENSIONS.get(extension.lower())


def should_count_loc(path, target_languages):
    parts = path.split("/")
    for part in parts:
        if part in SKIPPED_LOC_PATH_PARTS or part.startswith("cmake-build-"):
            return False

    lower_path = path.lower()
    if lower_path.endswith((".lock", ".map", ".min.css", ".min.js")):
        return False

    language = loc_language_for_path(path)
    return language in target_languages


def count_file_nonblank_lines(path):
    with open(path, "rb") as source_file:
        content = source_file.read()
    if b"\0" in content:
        return 0

    text = content.decode("utf-8", errors="ignore")
    return sum(1 for line in text.splitlines() if line.strip())


def clone_public_repo(repo, destination):
    clone_url = repo.get("clone_url") or f"https://github.com/{OWNER}/{repo['name']}.git"
    subprocess.run(
        ["git", "clone", "--depth", "1", "--single-branch", clone_url, destination],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )


def repo_loc_revision(repo):
    signature = "|".join(
        [
            repo.get("default_branch", ""),
            repo.get("pushed_at", ""),
            str(repo.get("size", "")),
        ]
    )
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]


def count_repo_loc(repo, target_languages, clone_root):
    totals = {}
    name = repo["name"]
    clone_path = os.path.join(clone_root, name)
    clone_public_repo(repo, clone_path)

    try:
        for root, dirnames, filenames in os.walk(clone_path):
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if dirname not in SKIPPED_LOC_PATH_PARTS and not dirname.startswith("cmake-build-")
            ]

            for filename in filenames:
                path = os.path.join(root, filename)
                rel_path = os.path.relpath(path, clone_path)
                if not should_count_loc(rel_path, target_languages):
                    continue
                if os.path.getsize(path) > MAX_LOC_FILE_BYTES:
                    continue

                language = loc_language_for_path(rel_path)
                totals[language] = totals.get(language, 0) + count_file_nonblank_lines(path)
    finally:
        shutil.rmtree(clone_path, ignore_errors=True)

    return totals


def fetch_language_loc_totals(repos, languages_by_repo, target_languages):
    totals = {}
    cache = read_json_file(LANGUAGE_LOC_CACHE_PATH, {"version": 1, "repos": {}})
    cached_repos = cache.get("repos", {})
    next_cache = {"version": 1, "repos": {}}
    target_repos = [
        repo for repo in repos if set(languages_by_repo.get(repo["name"], {})).intersection(target_languages)
    ]
    counted = 0
    reused = 0

    with tempfile.TemporaryDirectory(prefix="profile-loc-") as temp_root:
        for index, repo in enumerate(target_repos, start=1):
            name = repo["name"]
            default_branch = repo.get("default_branch", "main")
            revision = repo_loc_revision(repo)

            cached = cached_repos.get(name)
            cached_revision = cached.get("revision") if cached else None
            if (
                not FORCE_LANGUAGE_LOC
                and cached
                and cached.get("default_branch") == default_branch
                and (cached_revision == revision or (cached_revision is None and cached.get("loc")))
            ):
                repo_loc = cached.get("loc", {})
                reused += 1
            else:
                print(f"Counting LOC for public repository {index}/{len(target_repos)}: {name}", flush=True)
                try:
                    repo_loc = count_repo_loc(repo, target_languages, temp_root)
                except (subprocess.SubprocessError, OSError) as error:
                    cached = cached_repos.get(name)
                    if cached:
                        print(f"Reusing cached LOC for {name}; recount failed: {error}", file=sys.stderr)
                        repo_loc = cached.get("loc", {})
                        reused += 1
                    else:
                        print(f"Skipping LOC for {name}: {error}", file=sys.stderr)
                        continue
                else:
                    counted += 1

            repo_loc = {language: int(line_count) for language, line_count in repo_loc.items() if int(line_count) > 0}
            next_cache["repos"][name] = {
                "default_branch": default_branch,
                "revision": revision,
                "loc": repo_loc,
            }

            for language, line_count in repo_loc.items():
                totals[language] = totals.get(language, 0) + line_count

    if reused or counted:
        print(f"LOC cache: reused {reused}, counted {counted}")

    cache_changed = write_json_file(LANGUAGE_LOC_CACHE_PATH, next_cache)
    return totals, cache_changed


def format_percent(value):
    text = f"{value:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return f"{text}%"


def format_loc(value):
    if not value:
        return "n/a"
    return f"{value:,}"


def render_language_svg(language_totals, loc_totals):
    top_languages = sorted(language_totals.items(), key=lambda item: item[1], reverse=True)[:MAX_LANGUAGES]
    width = 640
    row_height = 28
    height = 110 + max(1, len(top_languages)) * row_height
    total = sum(language_totals.values())

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" role="img" aria-labelledby="title desc" viewBox="0 0 {width} {height}">',
        '<title id="title">Top public repository languages</title>',
        '<desc id="desc">Language breakdown and approximate nonblank LOC for public profile repositories.</desc>',
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="6" fill="#ffffff" stroke="#d0d7de"/>',
        '<text x="20" y="30" fill="#24292f" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="16" font-weight="600">Top Public Repo Languages</text>',
        '<text x="20" y="50" fill="#57606a" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="12">Byte share with approximate nonblank LOC</text>',
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
    chart_y = 68
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

    parts.extend(
        [
            '<text x="490" y="100" fill="#6e7781" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="11" text-anchor="end">Share</text>',
            '<text x="620" y="100" fill="#6e7781" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="11" text-anchor="end">LOC</text>',
        ]
    )

    for index, (language, byte_count) in enumerate(top_languages):
        percent = format_percent(byte_count * 100 / total)
        loc = format_loc(loc_totals.get(language, 0))
        y = 126 + index * row_height
        color = LANGUAGE_COLORS.get(language, "#6e7781")
        parts.extend(
            [
                f'<circle cx="26" cy="{y - 5}" r="5" fill="{color}"/>',
                f'<text x="40" y="{y}" fill="#24292f" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="13">{escape(language)}</text>',
                f'<text x="490" y="{y}" fill="#57606a" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="13" text-anchor="end">{percent}</text>',
                f'<text x="620" y="{y}" fill="#57606a" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="13" text-anchor="end">{loc}</text>',
            ]
        )

    parts.append("</svg>")
    return "\n".join(parts)


def write_language_svg(repos):
    safe_repos = public_profile_repositories(repos)
    language_totals, languages_by_repo = fetch_language_data(safe_repos)
    top_languages = sorted(language_totals, key=language_totals.get, reverse=True)[:MAX_LANGUAGES]
    loc_totals, cache_changed = fetch_language_loc_totals(safe_repos, languages_by_repo, set(top_languages))
    svg = render_language_svg(language_totals, loc_totals)
    svg_with_newline = f"{svg}\n"
    os.makedirs(os.path.dirname(LANGUAGE_SVG_PATH), exist_ok=True)

    current = None
    if os.path.exists(LANGUAGE_SVG_PATH):
        with open(LANGUAGE_SVG_PATH, "r", encoding="utf-8") as language_svg:
            current = language_svg.read()

    if svg_with_newline != current:
        with open(LANGUAGE_SVG_PATH, "w", encoding="utf-8") as language_svg:
            language_svg.write(svg_with_newline)
        return True
    return cache_changed


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
