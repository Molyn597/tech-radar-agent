"""Telegram integration — sends Markdown messages, chunking when over 4000 chars."""

from __future__ import annotations

import logging
from typing import Any

import requests

from config import (
    CHAT_ID,
    THREAD_ID,
    TELEGRAM_MAX_CHARS,
    HTTP_TIMEOUT,
    get_telegram_token,
    get_proposals_thread_id,
)

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/{method}"


def _api(method: str) -> str:
    return API_BASE.format(token=get_telegram_token(), method=method)


def send_message(text: str, *, chat_id: int | None = None, thread_id: int | None = None,
                 parse_mode: str = "Markdown") -> dict[str, Any] | None:
    """Send a single message. Returns the Telegram API JSON or None on failure."""
    payload = {
        "chat_id": chat_id if chat_id is not None else CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if (thread_id if thread_id is not None else THREAD_ID) is not None:
        payload["message_thread_id"] = thread_id if thread_id is not None else THREAD_ID
    try:
        resp = requests.post(_api("sendMessage"), json=payload, timeout=HTTP_TIMEOUT)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if not resp.ok or not data.get("ok"):
            log.warning("telegram: sendMessage failed: %s", resp.text[:400])
            return None
        return data
    except requests.RequestException as exc:
        log.warning("telegram: sendMessage exception: %s", exc)
        return None


def _split_for_telegram(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    """Split text into Telegram-sized chunks, preferring paragraph boundaries."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        # Look for the last paragraph break within the budget
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = limit  # hard cut
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def send_markdown_chunks(text: str) -> list[dict[str, Any]]:
    """Send a (possibly long) Markdown message in Telegram-sized chunks."""
    parts = _split_for_telegram(text)
    results: list[dict[str, Any]] = []
    for i, part in enumerate(parts, 1):
        if len(parts) > 1:
            header = f"*({i}/{len(parts)})*\n"
            part = header + part
        result = send_message(part)
        results.append(result or {"ok": False, "error": "send_failed"})
    return results


def send_proposals_summary(proposals: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Send a short architecture-proposal summary to the optional proposals thread.

    Returns None (no-op, not an error) if TELEGRAM_PROPOSALS_THREAD_ID is unset or
    there are no proposals — callers should treat both as "nothing to send".
    """
    thread_id = get_proposals_thread_id()
    if thread_id is None or not proposals:
        return None

    lines = ["*🧠 Architecture Proposals*"]
    for p in proposals[:5]:
        ap = p.get("actionable_proposal", {}) or {}
        lines.append(
            f"• *{p.get('title', '(untitled)')}* "
            f"[{p.get('relevance', '?')}/{p.get('impact_feasibility', '?')}] — "
            f"{ap.get('summary', '')}"
        )
    text = "\n".join(lines)
    results: list[dict[str, Any]] = []
    for part in _split_for_telegram(text):
        result = send_message(part, thread_id=thread_id)
        results.append(result or {"ok": False, "error": "send_failed"})
    return results
