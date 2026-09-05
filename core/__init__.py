"""Core package — dedup and analyzer."""
from .dedup import filter_new, mark_seen
from .analyzer import synthesize

__all__ = ["filter_new", "mark_seen", "synthesize"]
