"""Configuration loader for tech_radar_agent.

Loads secrets strictly from OS environment, falling back to the Windows
HKCU\\Environment registry. No plaintext secrets in this file.
"""

from __future__ import annotations

import os
import platform
from typing import Optional

# Telegram target (loaded securely from environment / registry, no hardcoded chat IDs)
def get_chat_id() -> int:
    val = _get_secret("TELEGRAM_CHAT_ID")
    if not val:
        raise RuntimeError("Missing TELEGRAM_CHAT_ID in environment / registry.")
    return int(val)


def get_thread_id() -> Optional[int]:
    val = _get_secret("TELEGRAM_THREAD_ID")
    return int(val) if val else None


CHAT_ID = get_chat_id()
THREAD_ID = get_thread_id()

# Model preference (free-tier fallback chain for OpenRouter)
OPENROUTER_MODEL_CHAIN = (
    "minimax/minimax-m3:free",   # primary per spec (MiniMax 3 / openrouter free)
    "openrouter/free",           # generic OpenRouter free dispatcher
    "minimax/minimax-01:free",   # older MiniMax free id
)


def _read_winreg_env(name: str) -> Optional[str]:
    """Read a single user-level environment variable from the Windows registry.

    Uses the winreg stdlib module — no external deps, no shell-out, no tokens
    printed to logs. Returns None on non-Windows or if the key is missing.
    """
    if platform.system() != "Windows":
        return None
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value if isinstance(value, str) and value else None
    except (OSError, FileNotFoundError, ImportError):
        return None


def _get_secret(name: str, aliases: tuple[str, ...] = ()) -> Optional[str]:
    """Return the first non-empty value among name + its aliases.

    Looks up OS env first, then HKCU\\Environment as a Windows fallback.
    """
    candidates = (name, *aliases)
    for key in candidates:
        val = os.environ.get(key)
        if val:
            return val
    for key in candidates:
        val = _read_winreg_env(key)
        if val:
            return val
    return None


def _require_secret(name: str, aliases: tuple[str, ...] = ()) -> str:
    val = _get_secret(name, aliases)
    if not val:
        raise RuntimeError(
            f"Missing required secret: {name} "
            f"(aliases: {', '.join(aliases) if aliases else 'none'}). "
            f"Set it in the OS environment or HKCU\\Environment."
        )
    return val


def get_telegram_token() -> str:
    return _require_secret("TELEGRAM_BOT_TOKEN", aliases=("JOBS_TELEGRAM_BOT_TOKEN",))


def get_openrouter_key() -> str:
    return _require_secret("OPENROUTER_API_KEY")


# HTTP defaults
HTTP_TIMEOUT = 20  # seconds
USER_AGENT_GITHUB = "TechRadarBot/1.0 (+https://github.local/tech-radar)"
USER_AGENT_REDDIT = "TechRadarBot/1.0 (by /u/tech-radar-bot)"
USER_AGENT_GENERIC = "TechRadarBot/1.0"

# Dedup window
DEDUP_WINDOW_DAYS = 14
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "history.json")

# Telegram chunking
TELEGRAM_MAX_CHARS = 4000
