"""Integrations package — Telegram delivery."""
from .telegram import send_message, send_markdown_chunks

__all__ = ["send_message", "send_markdown_chunks"]
