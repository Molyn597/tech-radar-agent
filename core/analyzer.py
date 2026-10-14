"""Analyzer — turn raw signal into a Czech Telegram Markdown digest via OpenRouter.

The system prompt strictly instructs the model to filter and synthesize the
input into exactly four named sections in clean Czech Markdown suitable for
Telegram (no nested HTML, no unsupported Markdown).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import requests

from config import get_openrouter_key, HTTP_TIMEOUT, OPENROUTER_MODEL_CHAIN

# TOON shared serializer (path bootstrap so analyzer.py works from any CWD)
_COMMON_DIR = Path(__file__).resolve().parent.parent.parent / "common"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
try:
    from toon_serializer import (  # noqa: E402
        encode_toon,
        record_savings,
        format_savings_footer,
    )
except ImportError:
    # Optional shared module (../common); fall back to compact JSON without savings telemetry.
    def encode_toon(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    def record_savings(*args: Any) -> dict:
        return {}

    def format_savings_footer(*args: Any) -> str:
        return ""

log = logging.getLogger(__name__)

AGENT_NAME = "tech_radar_agent"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """You are an elite AI/ML technical intelligence editor for the Telegram Tech-Radar channel.
Your task: from the provided raw signals (GitHub releases, trending repos, Hacker News, Reddit),
filter the MOST CRITICAL high-signal developments and construct EXACTLY four sections in concise, high-density English,
formatted in clean Telegram Markdown (use `*bold*`, `_italic_`, inline code, no raw HTML, no markdown inside link text).

Exact output structure (no intro, no outro, no summary headers outside these four):

1) *🛠 Token & Infra Hacks*
   - Developments in LLM inference, quantization, token efficiency, caching, GPU/VRAM optimizations,
     serving runtimes (vLLM, LiteLLM, Unsloth, llama.cpp, Ollama). Concrete numbers, versions, speedups.

2) *🤖 Agentic Architectures & Frameworks*
   - Multi-agent patterns, autonomous workflows, tool-use interfaces, RAG architectures,
     orchestration frameworks, evaluation benchmarks, and reasoning patterns.

3) *🚀 GitHub Trending Repos*
   - Notable new or fast-rising developer repositories. Include `repo_name`, key capabilities,
     and 1-2 punchy sentences on why it matters.

4) *🌐 Critical AI News*
   - Industry-shifting announcements: new model weights, major benchmarks, breakthrough papers,
     and ecosystem shifts (strictly zero PR marketing fluff).

Rules:
- Write in clean, professional, high-density English.
- Always include a direct clickable link for every item (e.g. 🔗 [Open Repo / Article](URL) or 🔗 [Discussion](URL)).
- If a section has no high-signal developments today, output `_No major updates._` for that section.
- Do not add any introductory or concluding chatter.
- Stay within 3800 characters total.
"""


def _build_user_payload(github_signal: dict[str, Any], community_signal: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Compress raw signal into a TOON-encoded prompt block.

    Returns: (toon_payload, baseline_json_str, savings_dict)
    """
    payload = {
        "github": {
            "tracked_releases": [
                {
                    "repo": r.get("repo"),
                    "title": r.get("title"),
                    "tag": r.get("tag"),
                    "url": r.get("url"),
                    "summary": (r.get("summary") or "")[:300],
                }
                for r in github_signal.get("tracked_releases", [])
            ],
            "tracked_meta": [
                {
                    "repo": m.get("repo"),
                    "stars": m.get("stars"),
                    "description": m.get("description"),
                    "pushed_at": m.get("pushed_at"),
                }
                for m in github_signal.get("tracked_meta", [])
            ],
            "trending": [
                {
                    "repo": t.get("repo"),
                    "stars": t.get("stars"),
                    "description": t.get("description"),
                    "url": t.get("url"),
                }
                for t in github_signal.get("trending", [])
            ],
        },
        "community": {
            "reddit": [
                {
                    "source": r.get("source"),
                    "title": r.get("title"),
                    "score": r.get("score"),
                    "comments": r.get("comments"),
                    "url": r.get("url") or r.get("permalink"),
                }
                for r in community_signal.get("reddit", [])
            ],
            "hackernews": [
                {
                    "source": h.get("source"),
                    "title": h.get("title"),
                    "score": h.get("score"),
                    "comments": h.get("comments"),
                    "url": h.get("url") or h.get("permalink"),
                }
                for h in community_signal.get("hackernews", [])
            ],
        },
    }
    baseline_json = json.dumps(payload, ensure_ascii=False, indent=2)
    toon_payload = encode_toon(payload)
    # Pick the first model for telemetry pricing (chain starts with the cheapest)
    model_name = OPENROUTER_MODEL_CHAIN[0] if OPENROUTER_MODEL_CHAIN else "openrouter"
    savings = record_savings(AGENT_NAME, model_name, baseline_json, toon_payload)
    return toon_payload, baseline_json, savings


def _call_openrouter(prompt: str, model: str) -> str | None:
    """Single chat-completion call. Returns the assistant message or None."""
    api_key = get_openrouter_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.local/tech-radar",
        "X-Title": "Tech-Radar Agent",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 1400,
    }
    try:
        resp = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=HTTP_TIMEOUT + 20)
        if resp.status_code >= 400:
            log.warning("analyzer: openrouter %s -> %s: %s", model, resp.status_code, resp.text[:300])
            return None
        data = resp.json()
        msg = (data.get("choices") or [{}])[0].get("message", {}).get("content")
        if not msg:
            log.warning("analyzer: empty content from %s", model)
            return None
        return msg.strip()
    except (requests.RequestException, ValueError) as exc:
        log.warning("analyzer: openrouter call failed (%s): %s", model, exc)
        return None


def _fallback_template(github_signal: dict[str, Any], community_signal: dict[str, Any]) -> str:
    """Deterministic offline digest if the LLM is unreachable — keeps the channel alive."""
    lines: list[str] = []
    lines.append("*🛠 Token & Infra Vychytávky*")
    if github_signal.get("tracked_releases"):
        for r in github_signal["tracked_releases"][:5]:
            lines.append(f"• `{r.get('repo')}` {r.get('title')} — {r.get('url')}")
    else:
        lines.append("_Žádné novinky._")

    lines.append("")
    lines.append("*🤖 Agentní architektury & Frameworky*")
    agentish = [
        x for x in community_signal.get("reddit", []) + community_signal.get("hackernews", [])
        if any(kw in (x.get("title") or "").lower()
               for kw in ("agent", "multi-agent", "autonomous", "rag", "tool"))
    ][:5]
    if agentish:
        for it in agentish:
            lines.append(f"• {it.get('title')} — {it.get('url') or it.get('permalink')}")
    else:
        lines.append("_Žádné novinky._")

    lines.append("")
    lines.append("*🚀 GitHub Trending Repos*")
    if github_signal.get("trending"):
        for t in github_signal["trending"][:5]:
            desc = (t.get("description") or "").strip()
            stars = t.get("stars")
            star_s = f" ⭐{stars}" if isinstance(stars, int) else ""
            lines.append(f"• `{t.get('repo')}`{star_s} — {desc[:120]}\n  {t.get('url')}")
    else:
        lines.append("_Žádné novinky._")

    lines.append("")
    lines.append("*🌐 Zásadní AI Novinky*")
    big = [
        x for x in community_signal.get("hackernews", []) + community_signal.get("reddit", [])
        if x.get("score", 0) >= 50
    ][:5]
    if big:
        for it in big:
            lines.append(f"• {it.get('title')} — {it.get('url') or it.get('permalink')}")
    else:
        lines.append("_Žádné novinky._")

    return "\n".join(lines)


def synthesize(github_signal: dict[str, Any], community_signal: dict[str, Any]) -> str:
    """Return a Czech Markdown digest (4 sections). Never raises.

    The TOON-encoded payload is sent to the LLM, and a 1-line savings footer
    is appended to the final digest so the user sees per-run token savings.
    """
    toon_payload, _baseline_json, savings = _build_user_payload(github_signal, community_signal)
    user_msg = (
        "Zde je dnešní signál (TOON format - mensi token footprint). "
        "Vyfiltruj a sestav digest podle pravidel:\n\n"
        + toon_payload
    )

    last_err: str | None = None
    for model in OPENROUTER_MODEL_CHAIN:
        text = _call_openrouter(user_msg, model)
        if text:
            return text + format_savings_footer(savings)
        last_err = f"model={model} returned nothing"

    log.warning("analyzer: all models failed (%s) — using fallback template", last_err)
    return _fallback_template(github_signal, community_signal) + format_savings_footer(savings)
