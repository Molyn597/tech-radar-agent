"""tech_radar_agent — daily AI/ML digest for the Tech-Radar Telegram thread.

Flow: fetch (GitHub + community) -> dedup -> synthesize -> send -> mark seen.

Usage:
    python main.py                        # full run, sends to Telegram
    python main.py --dry-run              # prints digest to stdout, no Telegram
    python main.py --limit N              # cap items per source (default 30)
    python main.py --evaluate             # also generate architecture proposals
    python main.py --export-orchestrator  # print proposals as an Orchestrator briefing
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from config import CHAT_ID, THREAD_ID

from scrapers import fetch_github_signal, fetch_community_signal
from core import filter_new, mark_seen, synthesize, generate_proposals, format_proposals_markdown
from integrations import send_markdown_chunks, send_proposals_summary

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


def run(
    dry_run: bool = False,
    item_limit: int = 30,
    evaluate: bool = False,
    eval_model: str | None = None,
    export_orchestrator: bool = False,
) -> int:
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
    else:
        log.info("step=telegram")
        results = send_markdown_chunks(full)
        ok_count = sum(1 for r in results if r and r.get("ok"))
        log.info("telegram: %d/%d chunks ok", ok_count, len(results))
        if ok_count == 0:
            log.error("telegram: no chunks delivered")
            return 3

        log.info("step=mark_seen")
        mark_seen(new_items)

    if evaluate or export_orchestrator:
        log.info("step=evaluator")
        proposals = generate_proposals(signals["github"], signals["community"], model=eval_model)
        log.info("evaluator: %d proposals", len(proposals))

        if export_orchestrator:
            print("=" * 60)
            print("ORCHESTRATOR BRIEFING — Tech-Radar architecture proposals")
            print(f"generated_at={signals['github'].get('fetched_at', '')} count={len(proposals)}")
            print("=" * 60)
            print(format_proposals_markdown(proposals))

        if not dry_run and proposals:
            send_proposals_summary(proposals)

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
    parser.add_argument("--evaluate", "--proposals", action="store_true", dest="evaluate",
                        help="Run the architecture evaluator over today's high-signal items "
                             "and save actionable implementation proposals to data/proposals/.")
    parser.add_argument("--eval-model", type=str, default=None,
                        help="Override the LLM used for evaluation (default: first model in "
                             "config.OPENROUTER_MODEL_CHAIN).")
    parser.add_argument("--export-orchestrator", action="store_true",
                        help="Print a clean structured briefing of the proposals to stdout "
                             "for direct hand-off to an Orchestrator. Implies --evaluate.")
    args = parser.parse_args(argv)

    if args.json:
        gh = fetch_github_signal()
        co = fetch_community_signal()
        print(json.dumps({"github": gh, "community": co}, ensure_ascii=False, indent=2)[:20000])
        return 0

    try:
        return run(
            dry_run=args.dry_run,
            item_limit=args.limit,
            evaluate=args.evaluate or args.export_orchestrator,
            eval_model=args.eval_model,
            export_orchestrator=args.export_orchestrator,
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
