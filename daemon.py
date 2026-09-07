"""tech_radar_agent daemon — 24/7 systemd service for daily AI/ML digests.

Runs as a long-lived background process:
  - Schedules the digest at 08:15 Europe/Prague every day (DST-aware).
  - Optionally runs a Telegram bot (python-telegram-bot) accepting
    /start (intro) and /radar (trigger immediate digest) commands.
  - On Linux: started by deploy/tech_radar.service under systemd.
  - On Windows: runnable directly with `python daemon.py` for development.

All secrets are loaded via config.py (env / Windows HKCU\\Environment / .env).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import traceback
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

# Make local imports work regardless of CWD
_DAEMON_DIR = Path(__file__).resolve().parent
if str(_DAEMON_DIR) not in sys.path:
    sys.path.insert(0, str(_DAEMON_DIR))

from config import CHAT_ID, THREAD_ID  # noqa: E402

# Local digest pipeline (same one main.py uses)
from scrapers import fetch_github_signal, fetch_community_signal  # noqa: E402
from core import filter_new, mark_seen, synthesize, generate_proposals  # noqa: E402
from integrations import send_markdown_chunks, send_proposals_summary  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("tech_radar.daemon")

# ---------------------------------------------------------------------------
# Scheduling — pure stdlib (zoneinfo), no apscheduler dependency required.
# ---------------------------------------------------------------------------

DAILY_HOUR = 8
DAILY_MINUTE = 15
TZ_NAME = "Europe/Prague"
DEFAULT_ITEM_LIMIT = 30


def _now_prague() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(TZ_NAME))


def _seconds_until_next_run(now: datetime, hour: int, minute: int) -> float:
    """Seconds from `now` until the next occurrence of hour:minute in `now.tzinfo`.

    If today's slot has already passed, returns seconds until tomorrow's slot.
    """
    target_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target_today <= now:
        target = target_today + timedelta(days=1)
    else:
        target = target_today
    return max((target - now).total_seconds(), 1.0)


# ---------------------------------------------------------------------------
# Digest execution — re-uses main.run() so behaviour is identical to CLI.
# ---------------------------------------------------------------------------

def _trim(items: list, limit: int) -> list:
    return items[:limit] if limit > 0 else items


def _run_digest(item_limit: int = DEFAULT_ITEM_LIMIT) -> int:
    """Run a full digest cycle. Returns process exit code (0 = ok)."""
    log.info("digest: start (chat_id=%s thread_id=%s limit=%d)", CHAT_ID, THREAD_ID, item_limit)
    try:
        log.info("step=github_radar")
        gh = fetch_github_signal()
        log.info("step=community_radar")
        co = fetch_community_signal()

        flat = []
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

        if not new_flat:
            new_flat = flat  # keep context for synthesis
        else:
            new_flat = _trim(new_flat, item_limit)

        co_filtered = {
            **co,
            "reddit": [x for x in new_flat if x.get("source", "").startswith("r/")],
            "hackernews": [x for x in new_flat if x.get("source") == "Hacker News"],
        }
        signals = {"github": gh, "community": co_filtered}

        log.info("step=analyzer")
        digest = synthesize(signals["github"], signals["community"])
        if not digest:
            log.error("analyzer returned empty digest — aborting")
            return 2

        header = f"*⚡ Tech-Radar — Daily Briefing*\n_{signals['github'].get('fetched_at', '')}_"
        full = f"{header}\n\n{digest}"

        log.info("step=telegram")
        results = send_markdown_chunks(full)
        ok_count = sum(1 for r in results if r and r.get("ok"))
        log.info("telegram: %d/%d chunks ok", ok_count, len(results))
        if ok_count == 0:
            log.error("telegram: no chunks delivered")
            return 3

        log.info("step=mark_seen")
        mark_seen(new_flat)

        log.info("step=evaluator")
        proposals = generate_proposals(signals["github"], signals["community"])
        if proposals:
            log.info("evaluator: %d proposals", len(proposals))
            send_proposals_summary(proposals)

        log.info("done")
        return 0
    except Exception as exc:  # noqa: BLE001 — daemon must not die silently
        log.error("digest failed: %s\n%s", exc, traceback.format_exc())
        return 4


# ---------------------------------------------------------------------------
# Async scheduler — runs daily at 08:15 Europe/Prague.
# ---------------------------------------------------------------------------

class DailyScheduler:
    """Tiny asyncio scheduler that fires `_run_digest` every day at DAILY_HOUR:DAILY_MINUTE Europe/Prague."""

    def __init__(self, hour: int = DAILY_HOUR, minute: int = DAILY_MINUTE, item_limit: int = DEFAULT_ITEM_LIMIT):
        self.hour = hour
        self.minute = minute
        self.item_limit = item_limit
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    def request_stop(self) -> None:
        log.info("scheduler: stop requested")
        self._stop.set()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            now = _now_prague()
            wait_s = _seconds_until_next_run(now, self.hour, self.minute)
            log.info(
                "scheduler: next digest at %s %s (in %.0f s)",
                (_now_prague() + timedelta(seconds=wait_s)).isoformat(timespec="seconds"),
                TZ_NAME,
                wait_s,
            )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait_s)
            except asyncio.TimeoutError:
                # Reached the scheduled time — run the digest.
                log.info("scheduler: firing daily digest")
                rc = await asyncio.to_thread(_run_digest, self.item_limit)
                log.info("scheduler: digest rc=%d", rc)
            else:
                # Stop event was set during the wait
                return

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="tech_radar.scheduler")

    async def wait(self) -> None:
        if self._task:
            await self._task


# ---------------------------------------------------------------------------
# Telegram command interface (optional, enabled when --bot is passed).
# ---------------------------------------------------------------------------

async def _bot_command_radar(update, context) -> None:
    """`/radar` — run the digest on demand and reply with status."""
    chat = update.effective_chat
    log.info("bot: /radar from chat_id=%s", chat.id if chat else None)
    await context.bot.send_message(
        chat_id=chat.id,
        text="⚡ Tech-Radar: spouštím digest on-demand…",
        message_thread_id=getattr(chat, "is_forum", False) and chat.message_thread_id or None,
    )
    rc = await asyncio.to_thread(_run_digest, DEFAULT_ITEM_LIMIT)
    await context.bot.send_message(
        chat_id=chat.id,
        text=("✅ Digest doručen." if rc == 0 else f"⚠️ Digest skončil s rc={rc}."),
        message_thread_id=getattr(chat, "is_forum", False) and chat.message_thread_id or None,
    )


async def _bot_command_start(update, context) -> None:
    """`/start` — short intro for the bot."""
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "👋 Tech-Radar bot.\n"
            "Každý den v 08:15 Europe/Prague odešle AI/ML digest do tohoto topicu.\n"
            "Pro okamžitý digest napiš `/radar`."
        ),
    )


async def _run_bot(scheduler: DailyScheduler) -> None:
    """Start python-telegram-bot polling and the scheduler concurrently."""
    from telegram import Update  # noqa: WPS433
    from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes  # noqa: WPS433

    from config import get_telegram_token

    token = get_telegram_token()
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", _bot_command_start))
    app.add_handler(CommandHandler("radar", _bot_command_radar))

    scheduler.start()

    async def _post_init(app_) -> None:
        # Register signal handlers so systemd Stop -> clean shutdown.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                asyncio.get_running_loop().add_signal_handler(
                    sig, lambda: asyncio.create_task(app_.shutdown())
                )
            except (NotImplementedError, RuntimeError):
                # Windows / restricted envs — fall back to Ctrl+C only.
                pass

    app.post_init = _post_init
    log.info("bot: starting polling + scheduler")
    async with app:
        await app.start()
        # Wait until the app is shut down (by signal or by stop event).
        try:
            await asyncio.shield(app.updater.start_polling())
            stop_event = asyncio.Event()
            app.add_handler  # noqa: B018 — ensure handlers registered
            # Block until the bot is stopped.
            await asyncio.gather(scheduler.wait(), _wait_until_stopped(app))
        finally:
            log.info("bot: shutting down")
            await app.stop()


async def _wait_until_stopped(app_) -> None:
    """Block forever; resolved only when the Application stops (signal handler)."""
    while not app_._shutdown_done.is_set() if hasattr(app_, "_shutdown_done") else True:
        await asyncio.sleep(1.0)


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="tech_radar_agent daemon (24/7 systemd service)")
    p.add_argument("--bot", action="store_true",
                   help="Also run the python-telegram-bot command interface (/start, /radar).")
    p.add_argument("--once", action="store_true",
                   help="Run a single digest now and exit (no scheduler, no bot).")
    p.add_argument("--limit", type=int, default=DEFAULT_ITEM_LIMIT,
                   help="Max community items per run (default 30).")
    p.add_argument("--hour", type=int, default=DAILY_HOUR,
                   help=f"Hour of daily run in {TZ_NAME} (default {DAILY_HOUR}).")
    p.add_argument("--minute", type=int, default=DAILY_MINUTE,
                   help=f"Minute of daily run in {TZ_NAME} (default {DAILY_MINUTE}).")
    return p.parse_args(argv)


async def _main(argv: list[str]) -> int:
    args = _parse_args(argv)

    if args.once:
        return await asyncio.to_thread(_run_digest, args.limit)

    scheduler = DailyScheduler(hour=args.hour, minute=args.minute, item_limit=args.limit)

    if args.bot:
        # Signal handlers are registered inside _run_bot (post_init).
        await _run_bot(scheduler)
        return 0

    # Pure scheduler mode (default for systemd service).
    scheduler.start()
    stop_event = asyncio.Event()

    def _on_signal() -> None:
        log.info("signal: stop received")
        scheduler.request_stop()
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (NotImplementedError, RuntimeError):
            pass

    log.info(
        "daemon: scheduled daily %02d:%02d %s (systemd-friendly)",
        args.hour, args.minute, TZ_NAME,
    )
    await stop_event.wait()
    await scheduler.wait()
    log.info("daemon: exit")
    return 0


def main() -> None:
    rc = asyncio.run(_main(sys.argv[1:]))
    sys.exit(rc)


if __name__ == "__main__":
    main()
