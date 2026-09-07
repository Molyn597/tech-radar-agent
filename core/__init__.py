"""Core package — dedup, analyzer, and evaluator."""
from .dedup import filter_new, mark_seen
from .analyzer import synthesize
from .evaluator import generate_proposals, format_proposals_markdown

__all__ = ["filter_new", "mark_seen", "synthesize", "generate_proposals", "format_proposals_markdown"]
