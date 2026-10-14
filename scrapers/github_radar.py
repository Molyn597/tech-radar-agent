"""GitHub radar — trending repos + recent releases for key AI/ML tools.

Uses `requests` with a proper User-Agent and graceful per-endpoint fallbacks.
Never raises; on any failure it returns whatever it managed to collect so the
pipeline can still produce a digest (even if a section ends up empty).
"""

from __future__ import annotations

import logging
from typing import Any
from datetime import datetime, timezone

import requests

from config import HTTP_TIMEOUT, USER_AGENT_GITHUB

log = logging.getLogger(__name__)

# Tracked tools (owner/repo) — popular LLM/agent infra
TRACKED_REPOS = [
    "BerriAI/litellm",
    "vllm-project/vllm",
    "unslothai/unsloth",
    "ggerganov/llama.cpp",
    "ollama/ollama",
]

HEADERS = {
    "User-Agent": USER_AGENT_GITHUB,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Anonymous GitHub API: 60 req/hour/IP. We make a small fixed number of calls
# per run, well under the budget for a daily digest.


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_get(url: str, params: dict[str, Any] | None = None, timeout: int = HTTP_TIMEOUT) -> dict | list | None:
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
        if resp.status_code == 403:
            # Rate limit or forbidden — log and bail for this URL
            log.warning("github_radar: %s -> 403 (rate limit?)", url)
            return None
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("github_radar: GET %s failed: %s", url, exc)
        return None


def fetch_repo_releases(full_name: str, limit: int = 3) -> list[dict[str, Any]]:
    """Return recent releases for a given owner/repo."""
    url = f"https://api.github.com/repos/{full_name}/releases"
    data = _safe_get(url, params={"per_page": limit})
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for rel in data[:limit]:
        out.append({
            "repo": full_name,
            "title": rel.get("name") or rel.get("tag_name") or "(untitled)",
            "tag": rel.get("tag_name", ""),
            "url": rel.get("html_url", ""),
            "published_at": rel.get("published_at", ""),
            "summary": (rel.get("body") or "").strip()[:600],
        })
    return out


def fetch_repo_meta(full_name: str) -> dict[str, Any] | None:
    """Lightweight repo metadata (stars, description, pushed_at)."""
    url = f"https://api.github.com/repos/{full_name}"
    data = _safe_get(url)
    if not isinstance(data, dict):
        return None
    return {
        "repo": full_name,
        "stars": data.get("stargazers_count", 0),
        "description": (data.get("description") or "").strip(),
        "pushed_at": data.get("pushed_at", ""),
        "url": data.get("html_url", ""),
    }


def fetch_trending_python() -> list[dict[str, Any]]:
    """Best-effort trending Python repos via the public github-trending mirror.

    Falls back to GitHub search (created:>now-7d, language:python) if the mirror
    is unreachable. Returns a small list of repos with star counts.
    """
    items: list[dict[str, Any]] = []

    # Strategy 1: github-trending HTML scrape (no API quota)
    try:
        resp = requests.get(
            "https://github.com/trending/python?since=daily",
            headers={"User-Agent": USER_AGENT_GITHUB},
            timeout=HTTP_TIMEOUT,
        )
        if resp.ok:
            # Very lightweight parse — repo names are the links inside <h2 class="h3 ..."> headings
            import re
            hrefs = re.findall(r'<h2 class="h3[^"]*">\s*<a [^>]*?href="/([^"/]+/[^"/]+)"', resp.text)
            seen: set[str] = set()
            for full in hrefs:
                if full not in seen:
                    seen.add(full)
                    items.append({"repo": full, "url": f"https://github.com/{full}"})
                    if len(items) >= 10:
                        break
    except requests.RequestException as exc:
        log.warning("github_radar: trending HTML scrape failed: %s", exc)

    # Strategy 2 fallback: GitHub search (if mirror gave nothing)
    if not items:
        search = _safe_get(
            "https://api.github.com/search/repositories",
            params={
                "q": "language:python created:>7-days-ago",
                "sort": "stars",
                "order": "desc",
                "per_page": 10,
            },
        )
        if isinstance(search, dict):
            for repo in search.get("items", [])[:10]:
                items.append({
                    "repo": repo.get("full_name", ""),
                    "stars": repo.get("stargazers_count", 0),
                    "description": (repo.get("description") or "").strip(),
                    "url": repo.get("html_url", ""),
                })

    return items[:10]


def fetch_github_signal() -> dict[str, Any]:
    """Aggregate GitHub signal for the digest.

    Returns a dict with keys: `tracked_releases`, `tracked_meta`, `trending`.
    Never raises.
    """
    tracked_releases: list[dict[str, Any]] = []
    tracked_meta: list[dict[str, Any]] = []

    for full_name in TRACKED_REPOS:
        try:
            meta = fetch_repo_meta(full_name)
            if meta:
                tracked_meta.append(meta)
            rels = fetch_repo_releases(full_name, limit=2)
            tracked_releases.extend(rels)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("github_radar: error while processing %s: %s", full_name, exc)

    trending = fetch_trending_python()

    return {
        "fetched_at": _iso_now(),
        "tracked_repos": TRACKED_REPOS,
        "tracked_releases": tracked_releases,
        "tracked_meta": tracked_meta,
        "trending": trending,
    }
