"""tech_radar_agent — daily AI/ML digest for the Tech-Radar Telegram thread.

Flow: fetch (GitHub + community) -> dedup -> synthesize -> send -> mark seen.

Usage:
    python main.py                # full run, sends to Telegram
    python main.py --dry-run      # prints digest to stdout, no Telegram
    python main.py --limit N      # cap items per source (default 30)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from config import CHAT_ID, THREAD_ID

from scrapers import fetch_github_signal, fetch_community_signal
from core import filter_new, mark_seen, synthesize
from integrations import send_markdown_chunks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("tech_radar")


def _trim(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return items[:limit] if limit > 0 else items


def collect_signal(item_limit: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run scrapers, dedup, return (raw_signals, surviving_items_to_send_to_LLM)."""
    log.info("step=github_radar")
    gh = fetch_github_signal()
    log.info("step=community_radar")
    co = fetch_community_signal()

    # Build the canonical flat list of community items we want the model to see
    flat: list[dict[str, Any]] = []
    for it in co.get("merged", []):
        flat.append({
            "id": it.get("id", ""),
            "source": it.get("source", ""),
            "title": it.get("title", ""),
            "url": it.get("url", "") or it.get("permalink", ""),
            "permalink": it.get("permalink", ""),
            "score": it.get("score", 0),
            "comments": it.get("comments", 0),
            "created_utc": it.get("created_utc", 0),
        })

    flat = _trim(flat, item_limit)
    new_flat = filter_new(flat)
    log.info("dedup: %d -> %d new items", len(flat), len(new_flat))

    # Even if dedup filters everything out, still build a digest from GitHub + recent items
    if not new_flat:
        log.info("no new community items — using full window for synthesis")
        new_flat = flat  # so the model has context
    else:
        new_flat = _trim(new_flat, item_limit)

    # Splice the deduped community items back into the community signal dict
    co_filtered = {**co, "reddit": [x for x in new_flat if x.get("source", "").startswith("r/")],
                   "hackernews": [x for x in new_flat if x.get("source") == "Hacker News"]}

    return {"github": gh, "community": co_filtered}, new_flat


def run(dry_run: bool = False, item_limit: int = 30) -> int:
    log.info("start chat_id=%s thread_id=%s dry_run=%s limit=%d",
             CHAT_ID, THREAD_ID, dry_run, item_limit)
    signals, new_items = collect_signal(item_limit)

    log.info("step=analyzer")
    digest = synthesize(signals["github"], signals["community"])
    if not digest:
        log.error("analyzer returned empty digest — aborting")
        return 2

    header = f"*⚡ Tech-Radar — Daily Briefing*\n_{signals['github'].get('fetched_at', '')}_"
    full = f"{header}\n\n{digest}"

    if dry_run:
        print("=" * 60)
        print("DRY-RUN — Telegram call skipped")
        print(f"chat_id={CHAT_ID} thread_id={THREAD_ID}")
        print(f"items considered: {len(new_items)}")
        print(f"digest length: {len(full)} chars")
        print("=" * 60)
        print(full)
        return 0

    log.info("step=telegram")
    results = send_markdown_chunks(full)
    ok_count = sum(1 for r in results if r and r.get("ok"))
    log.info("telegram: %d/%d chunks ok", ok_count, len(results))
    if ok_count == 0:
        log.error("telegram: no chunks delivered")
        return 3

    log.info("step=mark_seen")
    mark_seen(new_items)
    log.info("done")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tech-Radar daily digest agent")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip Telegram delivery, print digest to stdout.")
    parser.add_argument("--limit", type=int, default=30,
                        help="Max community items to consider (default 30).")
    parser.add_argument("--json", action="store_true",
                        help="Dump the raw scraped signal to stdout (debug).")
    args = parser.parse_args(argv)

    if args.json:
        gh = fetch_github_signal()
        co = fetch_community_signal()
        print(json.dumps({"github": gh, "community": co}, ensure_ascii=False, indent=2)[:20000])
        return 0

    try:
        return run(dry_run=args.dry_run, item_limit=args.limit)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
