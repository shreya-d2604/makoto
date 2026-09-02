"""Comparative LLM ranking of merged PRs, with a deterministic fallback."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import BaseModel

from fetch import PRRecord

RANKING_CACHE_PATH = Path(".cache/ranking.json")
BODY_TRUNCATE_CHARS = 800
MODEL = "gemini-3.6-flash"


@dataclass
class RankedPR:
    url: str
    rank: int
    reason: str
    bullet: str | None = None


class RankingItem(BaseModel):
    url: str
    rank: int
    reason: str
    bullet: str


class RankingResponse(BaseModel):
    rankings: list[RankingItem]


class RankingError(RuntimeError):
    """Raised when the LLM ranking call fails, with a clear explanation."""


def get_llm_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def _candidate_key(records: list[PRRecord], rubric_version: str) -> str:
    digest = hashlib.sha256()
    digest.update(rubric_version.encode())
    for url in sorted(r.url for r in records):
        digest.update(url.encode())
    return digest.hexdigest()


def _load_ranking_cache(path: Path = RANKING_CACHE_PATH) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _save_ranking_cache(
    key: str, rankings: list[RankedPR], used_llm: bool, path: Path = RANKING_CACHE_PATH
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"key": key, "used_llm": used_llm, "rankings": [asdict(r) for r in rankings]}
    path.write_text(json.dumps(payload, indent=2))


def deterministic_rank(records: list[PRRecord]) -> list[RankedPR]:
    """Fallback used when no LLM API key is set: log-scaled stars, then recency."""

    def sort_key(r: PRRecord) -> tuple[float, str]:
        return (math.log1p(r.repo_stars), r.merged_at)

    ordered = sorted(records, key=sort_key, reverse=True)
    return [
        RankedPR(
            url=r.url,
            rank=i + 1,
            reason="Deterministic fallback ranking: log-scaled repo stars, then recency.",
        )
        for i, r in enumerate(ordered)
    ]


def _build_prompt(records: list[PRRecord], rubric: str) -> str:
    candidates = [
        {
            "url": r.url,
            "repo": r.repo_full_name,
            "repo_description": r.repo_description,
            "repo_stars": r.repo_stars,
            "title": r.title,
            "body": (r.body or "")[:BODY_TRUNCATE_CHARS],
            "files_changed": r.changed_files,
            "additions": r.additions,
            "deletions": r.deletions,
            "review_comments": r.review_comments,
        }
        for r in records
    ]

    return (
        f"{rubric}\n\n"
        "Below is a JSON array of a developer's merged pull requests. Rank them against "
        "each other, most impressive first (rank 1 = most impressive). Every PR in the "
        "input must appear exactly once in your output, each with its rank and a one-line "
        "reason for that placement.\n\n"
        "Also write a resume bullet for each PR, following these rules exactly:\n"
        "- One line, under 100 characters (a ' (repo #123)' suffix will be appended after, "
        "so leave room)\n"
        "- Start with a past-tense action verb\n"
        "- Be concrete about what changed, not vague (e.g. 'Fixed race condition in shutdown "
        "path', not 'Improved error handling')\n"
        "- No first person, no 'I'\n\n"
        f"{json.dumps(candidates, indent=2)}"
    )


def _call_model(client, prompt: str) -> list[RankingItem]:
    from google.genai import errors, types

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RankingResponse,
            ),
        )
    except errors.ClientError as exc:
        raise RankingError(f"Gemini API rejected the request (check GEMINI_API_KEY): {exc.message}") from exc
    except errors.ServerError as exc:
        raise RankingError(f"Gemini API server error: {exc.message}") from exc
    except errors.APIError as exc:
        raise RankingError(f"Gemini API error: {exc.message}") from exc
    except Exception as exc:  # network errors etc. from the underlying HTTP client
        raise RankingError(f"Network error calling Gemini API: {exc}") from exc

    parsed: RankingResponse | None = response.parsed
    if parsed is None:
        raise RankingError("Gemini API returned no parseable structured output.")

    return parsed.rankings


def llm_rank(records: list[PRRecord], rubric: str, api_key: str) -> list[RankedPR]:
    from google import genai

    client = genai.Client(api_key=api_key)
    prompt = _build_prompt(records, rubric)
    input_urls = {r.url for r in records}

    items = _call_model(client, prompt)
    returned_urls = {i.url for i in items}

    if returned_urls != input_urls:
        items = _call_model(client, prompt)  # retry once
        returned_urls = {i.url for i in items}

    result = [
        RankedPR(url=i.url, rank=i.rank, reason=i.reason, bullet=i.bullet)
        for i in items
        if i.url in input_urls
    ]

    missing = input_urls - returned_urls
    if missing:
        by_url = {r.url: r for r in records}
        tail = sorted((by_url[u] for u in missing), key=lambda r: r.merged_at)
        next_rank = len(result) + 1
        for r in tail:
            result.append(
                RankedPR(
                    url=r.url,
                    rank=next_rank,
                    reason="Appended: the model omitted this PR from its ranking output.",
                )
            )
            next_rank += 1

    return result


def rank_prs(
    records: list[PRRecord], config: dict, *, rejudge: bool = False
) -> tuple[list[RankedPR], bool]:
    """Rank all non-excluded PRs. Returns (rankings, used_llm)."""
    ranking_cfg = config.get("ranking", {})
    excluded = set(ranking_cfg.get("excluded", []))
    rubric = ranking_cfg.get("rubric", "")
    rubric_version = str(ranking_cfg.get("rubric_version", "v1"))

    candidates = [r for r in records if r.url not in excluded]
    key = _candidate_key(candidates, rubric_version)

    if not rejudge:
        cached = _load_ranking_cache()
        if cached and cached.get("key") == key:
            return [RankedPR(**item) for item in cached["rankings"]], bool(cached.get("used_llm", True))

    api_key = get_llm_key()
    if api_key:
        rankings = llm_rank(candidates, rubric, api_key)
        used_llm = True
    else:
        print(
            "Notice: GEMINI_API_KEY is not set -- using deterministic ranking "
            "(log-scaled repo stars, then recency) instead of LLM judgment."
        )
        rankings = deterministic_rank(candidates)
        used_llm = False

    _save_ranking_cache(key, rankings, used_llm)
    return rankings, used_llm


def select_top(rankings: list[RankedPR], pinned: list[str], n: int) -> list[RankedPR]:
    """Pick the top n, with pinned URLs always included and filling from the top of rank order."""
    by_url = {r.url: r for r in rankings}
    pinned_present = sorted((by_url[u] for u in pinned if u in by_url), key=lambda r: r.rank)[:n]
    pinned_urls = {r.url for r in pinned_present}

    rest_sorted = sorted((r for r in rankings if r.url not in pinned_urls), key=lambda r: r.rank)
    remaining_slots = max(0, n - len(pinned_present))

    selected = pinned_present + rest_sorted[:remaining_slots]
    return sorted(selected, key=lambda r: r.rank)[:n]
