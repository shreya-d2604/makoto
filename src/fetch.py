"""GitHub API: find merged PRs for a user and cache their full details."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

GITHUB_API = "https://api.github.com"
CACHE_PATH = Path(".cache/prs.json")


@dataclass
class PRRecord:
    repo_full_name: str
    repo_stars: int
    repo_description: str
    number: int
    title: str
    body: str
    merged_at: str
    changed_files: int
    additions: int
    deletions: int
    review_comments: int
    url: str


class GitHubError(RuntimeError):
    """Raised when a GitHub API call fails, with a clear explanation."""


def _session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )
    return session


def _get(session: requests.Session, url: str, params: dict | None = None) -> requests.Response:
    try:
        response = session.get(url, params=params, timeout=30)
    except requests.RequestException as exc:
        raise GitHubError(f"Network error calling GitHub API ({url}): {exc}") from exc

    if response.status_code == 401:
        raise GitHubError("GitHub rejected the token (401 Unauthorized). Check GITHUB_TOKEN.")
    if response.status_code == 403:
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining == "0":
            raise GitHubError("GitHub API rate limit exceeded. Wait and retry, or use a token with a higher limit.")
        raise GitHubError(f"GitHub API returned 403 Forbidden for {url}: {response.text[:200]}")
    if not response.ok:
        raise GitHubError(f"GitHub API error {response.status_code} for {url}: {response.text[:200]}")

    return response


def search_merged_prs(username: str, session: requests.Session) -> list[dict]:
    """Paginate the search API fully for is:pr is:merged author:<username>."""
    items: list[dict] = []
    url: str | None = f"{GITHUB_API}/search/issues"
    params: dict | None = {
        "q": f"is:pr is:merged author:{username}",
        "per_page": 100,
        "page": 1,
    }

    while url:
        response = _get(session, url, params)
        payload = response.json()
        items.extend(payload.get("items", []))

        next_url = response.links.get("next", {}).get("url")
        url = next_url
        params = None  # next_url already carries all query params

    return items


def fetch_pr_detail(repo_full_name: str, number: int, session: requests.Session) -> dict:
    url = f"{GITHUB_API}/repos/{repo_full_name}/pulls/{number}"
    return _get(session, url).json()


def fetch_repo_info(
    repo_full_name: str, session: requests.Session, cache: dict[str, tuple[int, str]]
) -> tuple[int, str]:
    if repo_full_name not in cache:
        url = f"{GITHUB_API}/repos/{repo_full_name}"
        repo = _get(session, url).json()
        cache[repo_full_name] = (repo["stargazers_count"], repo.get("description") or "")
    return cache[repo_full_name]


def fetch_all_prs(username: str, token: str) -> list[PRRecord]:
    session = _session(token)
    search_results = search_merged_prs(username, session)

    repo_cache: dict[str, tuple[int, str]] = {}
    records: list[PRRecord] = []

    for item in search_results:
        # repository_url looks like https://api.github.com/repos/{owner}/{repo}
        repo_full_name = "/".join(item["repository_url"].split("/")[-2:])
        number = item["number"]

        detail = fetch_pr_detail(repo_full_name, number, session)
        stars, description = fetch_repo_info(repo_full_name, session, repo_cache)

        records.append(
            PRRecord(
                repo_full_name=repo_full_name,
                repo_stars=stars,
                repo_description=description,
                number=number,
                title=detail["title"],
                body=detail.get("body") or "",
                merged_at=detail["merged_at"],
                changed_files=detail["changed_files"],
                additions=detail["additions"],
                deletions=detail["deletions"],
                review_comments=detail["review_comments"],
                url=detail["html_url"],
            )
        )

    return records


def save_cache(records: list[PRRecord], path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(r) for r in records], indent=2))


def load_cache(path: Path = CACHE_PATH) -> list[PRRecord] | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    return [PRRecord(**item) for item in raw]
