"""Community radar — Reddit + Hacker News top stories.

Reddit: JSON endpoints for r/LocalLLaMA, r/MachineLearning, r/ArtificialIntelligence.
HN: Algolia search API for the previous ~24 hours.

All endpoints are read-only public APIs; User-Agent is mandatory for Reddit.
This module never raises — failures degrade gracefully.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

from config import HTTP_TIMEOUT, USER_AGENT_REDDIT

log = logging.getLogger(__name__)

REDDIT_SUBS = [
    "LocalLLaMA",
    "MachineLearning",
    "ArtificialIntelligence",
]
REDDIT_PER_SUB = 5

HN_LOOKBACK_HOURS = 24
HN_PER_QUERY = 8


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_get_json(url: str, headers: dict[str, str], params: dict[str, Any] | None = None,
                    timeout: int = HTTP_TIMEOUT) -> Any:
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("community_radar: GET %s failed: %s", url, exc)
        return None


def fetch_reddit_top() -> list[dict[str, Any]]:
    """Top daily posts from tracked subreddits via the public .json feed."""
    items: list[dict[str, Any]] = []
    headers = {"User-Agent": USER_AGENT_REDDIT}
    for sub in REDDIT_SUBS:
        url = f"https://www.reddit.com/r/{sub}/top.json"
        params = {"t": "day", "limit": REDDIT_PER_SUB}
        data = _safe_get_json(url, headers, params=params)
        if not isinstance(data, dict):
            continue
        children = data.get("data", {}).get("children", [])
        for child in children[:REDDIT_PER_SUB]:
            d = child.get("data") or {}
            if d.get("stickied") or d.get("over_18"):
                continue
            items.append({
                "source": f"r/{sub}",
                "id": f"reddit:{d.get('id', '')}",
                "title": (d.get("title") or "").strip(),
                "url": d.get("url_overridden_by_dest") or d.get("url") or "",
                "permalink": f"https://www.reddit.com{d.get('permalink', '')}",
                "score": d.get("score", 0),
                "comments": d.get("num_comments", 0),
                "created_utc": d.get("created_utc", 0),
                "summary": (d.get("selftext") or "").strip()[:500],
            })
    return items


def fetch_hn_top() -> list[dict[str, Any]]:
    """Top HN stories in the last 24 hours via Algolia search API."""
    items: list[dict[str, Any]] = []
    url = "https://hn.algolia.com/api/v1/search"
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(hours=HN_LOOKBACK_HOURS)).timestamp())
    params = {
        "tags": "story",
        "numericFilters": f"created_at_i>{cutoff_ts},points>10",
        "hitsPerPage": HN_PER_QUERY * 2,  # overfetch slightly
    }
    data = _safe_get_json(url, headers={"User-Agent": USER_AGENT_REDDIT}, params=params)
    if not isinstance(data, dict):
        return items
    seen: set[str] = set()
    for hit in data.get("hits", []):
        obj_id = hit.get("objectID", "")
        if not obj_id or obj_id in seen:
            continue
        seen.add(obj_id)
        items.append({
            "source": "Hacker News",
            "id": f"hn:{obj_id}",
            "title": (hit.get("title") or hit.get("story_title") or "").strip(),
            "url": hit.get("url") or f"https://news.ycombinator.com/item?id={obj_id}",
            "permalink": f"https://news.ycombinator.com/item?id={obj_id}",
            "score": hit.get("points", 0),
            "comments": hit.get("num_comments", 0),
            "created_utc": hit.get("created_at_i", 0),
            "summary": "",
        })
        if len(items) >= HN_PER_QUERY:
            break
    return items


def fetch_community_signal() -> dict[str, Any]:
    """Aggregate community signal. Never raises."""
    reddit = fetch_reddit_top()
    hn = fetch_hn_top()
    # Merge into a single ranked list (score-desc)
    merged = reddit + hn
    merged.sort(key=lambda x: x.get("score", 0), reverse=True)
    return {
        "fetched_at": _iso_now(),
        "reddit": reddit,
        "hackernews": hn,
        "merged": merged,
    }
