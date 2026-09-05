"""Dedup — track seen items in data/history.json within a sliding 14-day window."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

from config import HISTORY_FILE, DEDUP_WINDOW_DAYS

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_history() -> dict[str, Any]:
    """Read the history file, returning an empty store on any issue."""
    if not os.path.exists(HISTORY_FILE):
        return {"items": {}, "updated_at": _now_iso()}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"items": {}, "updated_at": _now_iso()}
        data.setdefault("items", {})
        return data
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("dedup: failed to read history (%s) — starting fresh", exc)
        return {"items": {}, "updated_at": _now_iso()}


def _save_history(store: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    store["updated_at"] = _now_iso()
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, HISTORY_FILE)


def _prune(store: dict[str, Any]) -> None:
    """Drop items older than the dedup window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=DEDUP_WINDOW_DAYS)
    items = store.get("items", {})
    stale = [
        key for key, entry in items.items()
        if isinstance(entry, dict)
        and datetime.fromisoformat(entry.get("seen_at", _now_iso()).replace("Z", "+00:00")) < cutoff
    ]
    for key in stale:
        items.pop(key, None)


def _item_id(item: dict[str, Any]) -> str:
    """Stable per-source id for an item."""
    if "id" in item and item["id"]:
        return str(item["id"])
    # Fallback: hash of url+title
    import hashlib
    h = hashlib.sha1()
    h.update((item.get("url") or "").encode("utf-8"))
    h.update(b"|")
    h.update((item.get("title") or "").encode("utf-8"))
    return "h:" + h.hexdigest()[:16]


def filter_new(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only items not seen within the dedup window."""
    store = _load_history()
    _prune(store)
    out: list[dict[str, Any]] = []
    for it in items:
        key = _item_id(it)
        if key in store["items"]:
            continue
        out.append(it)
    # Persist the prune even when nothing new
    _save_history(store)
    return out


def mark_seen(items: list[dict[str, Any]]) -> None:
    """Record items as seen (now). Idempotent."""
    if not items:
        return
    store = _load_history()
    _prune(store)
    ts = _now_iso()
    for it in items:
        key = _item_id(it)
        store["items"][key] = {
            "seen_at": ts,
            "title": it.get("title", ""),
            "url": it.get("url", ""),
            "source": it.get("source", ""),
        }
    _save_history(store)
