"""Scrapers package — fetches raw tech signal from GitHub, Reddit, HN."""
from .github_radar import fetch_github_signal
from .community_radar import fetch_community_signal

__all__ = ["fetch_github_signal", "fetch_community_signal"]
