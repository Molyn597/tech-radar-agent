"""Integrations package — Telegram delivery."""
from .telegram import send_message, send_markdown_chunks, send_proposals_summary

__all__ = ["send_message", "send_markdown_chunks", "send_proposals_summary"]
