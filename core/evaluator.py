"""Evaluator — turn high-signal GitHub/community items into actionable
implementation proposals for our own multi-agent system.

Flow: extract candidates (keyword-filtered) -> dedup -> deep-dive context
(GitHub README) -> LLM evaluation (JSON) -> persist to data/proposals/.

Never raises: any GitHub/OpenRouter failure degrades to an empty proposal
list so the core digest pipeline (main.py) keeps working unaffected.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from config import (
    get_openrouter_key,
    HTTP_TIMEOUT,
    OPENROUTER_MODEL_CHAIN,
    USER_AGENT_GITHUB,
    PROPOSALS_DIR,
    EVALUATOR_KEYWORDS,
    EVALUATOR_MAX_CANDIDATES,
    EVALUATOR_README_CHARS,
)
from .dedup import filter_new, mark_seen

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

AGENT_NAME = "tech_radar_agent.evaluator"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """You are a Senior AI Systems Architect embedded in a Python multi-agent
workspace (Orchestrator + specialized Coder subagents, Telegram bots, Windows/Mac hybrid,
strict token-budget discipline). You receive a shortlist of high-signal GitHub repos /
releases / community discussions about LLM infra, agent frameworks, and token efficiency.

Your job: for EACH candidate, produce a concrete, actionable evaluation of whether and how
to adopt it into OUR OWN stack. Focus areas to weigh explicitly:
- Token usage efficiency: context caching, prompt compression (we already use a TOON
  serializer + a Cavecrew-style compact format), local model offloading (LM Studio / vLLM /
  Ollama), token tracking/telemetry.
- Agentic workflow & delegation: task decomposition between an Orchestrator and subagents,
  tool-calling pipelines, structured outputs, minimizing round-trips.
- Practical applicability: no essays. Every proposal must name which of our modules/scripts
  it touches and what the estimated benefit is (tokens saved, latency, dev time).

Return ONLY a JSON array (no prose, no markdown fences, no keys outside the schema below).
One object per candidate you were given, in the same order:

[
  {
    "candidate_id": "<echo the id field you were given>",
    "title": "<short repo/item name>",
    "source_url": "<url>",
    "category": "token_efficiency" | "agentic_workflow" | "other",
    "relevance": "Low" | "Medium" | "High",
    "impact_feasibility": "Low" | "Medium" | "High",
    "actionable_proposal": {
      "summary": "<one sentence, what to do>",
      "target_module": "<file(s)/module(s) to create or edit>",
      "steps": ["<concrete step 1>", "<concrete step 2>"],
      "estimated_benefit": "<e.g. ~30% token reduction on synth calls, or 'low value, skip'>"
    }
  }
]

If a candidate is not actually relevant to our stack, still include it with
relevance="Low" and a one-line honest "skip" rationale in estimated_benefit — never omit
an item you were given.
"""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _matches_keywords(*texts: str) -> bool:
    blob = " ".join(t for t in texts if t).lower()
    return any(kw in blob for kw in EVALUATOR_KEYWORDS)


def _extract_candidates(github_signal: dict[str, Any], community_signal: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize GitHub + community signal into a flat, keyword-filtered candidate list."""
    candidates: list[dict[str, Any]] = []

    for rel in github_signal.get("tracked_releases", []):
        repo = rel.get("repo", "")
        title = rel.get("title", "")
        summary = rel.get("summary", "")
        if not repo or not _matches_keywords(repo, title, summary):
            continue
        candidates.append({
            "id": f"eval:release:{repo}:{rel.get('tag', title)}",
            "kind": "github_release",
            "repo": repo,
            "title": title,
            "url": rel.get("url", ""),
            "text": summary,
        })

    for meta in github_signal.get("tracked_meta", []):
        repo = meta.get("repo", "")
        desc = meta.get("description", "")
        if not repo or not _matches_keywords(repo, desc):
            continue
        candidates.append({
            "id": f"eval:meta:{repo}",
            "kind": "github_repo",
            "repo": repo,
            "title": repo,
            "url": meta.get("url", ""),
            "text": desc,
        })

    for trend in github_signal.get("trending", []):
        repo = trend.get("repo", "")
        desc = trend.get("description", "")
        if not repo or not _matches_keywords(repo, desc):
            continue
        candidates.append({
            "id": f"eval:trending:{repo}",
            "kind": "github_trending",
            "repo": repo,
            "title": repo,
            "url": trend.get("url", ""),
            "text": desc,
        })

    for it in community_signal.get("reddit", []) + community_signal.get("hackernews", []):
        title = it.get("title", "")
        if not title or not _matches_keywords(title):
            continue
        candidates.append({
            "id": f"eval:community:{it.get('id', title)}",
            "kind": "community",
            "repo": "",
            "title": title,
            "url": it.get("url") or it.get("permalink", ""),
            "text": it.get("summary", ""),
        })

    return candidates


def _fetch_readme_excerpt(repo: str) -> str:
    """Best-effort README fetch for a GitHub repo. Empty string on any failure."""
    if not repo:
        return ""
    url = f"https://api.github.com/repos/{repo}/readme"
    headers = {
        "User-Agent": USER_AGENT_GITHUB,
        "Accept": "application/vnd.github+json",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        if not resp.ok:
            return ""
        data = resp.json()
        content_b64 = data.get("content", "")
        if not content_b64:
            return ""
        raw = base64.b64decode(content_b64, validate=False)
        text = raw.decode("utf-8", errors="ignore")
        return text[:EVALUATOR_README_CHARS]
    except (requests.RequestException, ValueError, UnicodeDecodeError) as exc:
        log.warning("evaluator: readme fetch failed for %s: %s", repo, exc)
        return ""


def _enrich_with_deep_context(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add a README excerpt to GitHub-sourced candidates (best-effort, never raises)."""
    for c in candidates:
        if c["kind"] in ("github_release", "github_repo", "github_trending") and c.get("repo"):
            excerpt = _fetch_readme_excerpt(c["repo"])
            if excerpt:
                c["readme_excerpt"] = excerpt
    return candidates


def _build_llm_payload(candidates: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    payload = {
        "candidates": [
            {
                "id": c["id"],
                "kind": c["kind"],
                "repo": c.get("repo", ""),
                "title": c.get("title", ""),
                "url": c.get("url", ""),
                "text": (c.get("text") or "")[:400],
                "readme_excerpt": (c.get("readme_excerpt") or "")[:EVALUATOR_README_CHARS],
            }
            for c in candidates
        ]
    }
    baseline_json = json.dumps(payload, ensure_ascii=False, indent=2)
    toon_payload = encode_toon(payload)
    model_name = OPENROUTER_MODEL_CHAIN[0] if OPENROUTER_MODEL_CHAIN else "openrouter"
    savings = record_savings(AGENT_NAME, model_name, baseline_json, toon_payload)
    return toon_payload, baseline_json, savings


def _call_openrouter_json(prompt: str, model: str) -> str | None:
    api_key = get_openrouter_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.local/tech-radar",
        "X-Title": "Tech-Radar Evaluator",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 2200,
    }
    try:
        resp = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=HTTP_TIMEOUT + 20)
        if resp.status_code >= 400:
            log.warning("evaluator: openrouter %s -> %s: %s", model, resp.status_code, resp.text[:300])
            return None
        data = resp.json()
        msg = (data.get("choices") or [{}])[0].get("message", {}).get("content")
        if not msg:
            log.warning("evaluator: empty content from %s", model)
            return None
        return msg.strip()
    except (requests.RequestException, ValueError) as exc:
        log.warning("evaluator: openrouter call failed (%s): %s", model, exc)
        return None


def _parse_proposals_json(raw: str) -> list[dict[str, Any]] | None:
    """Lenient JSON-array parse — tolerates stray markdown fences around the array."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def _save_proposals(proposals: list[dict[str, Any]]) -> str:
    os.makedirs(PROPOSALS_DIR, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    json_path = os.path.join(PROPOSALS_DIR, f"{date_str}_proposals.json")
    record = {"generated_at": _iso_now(), "proposals": proposals}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    md_path = os.path.join(PROPOSALS_DIR, f"{date_str}_proposals.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(format_proposals_markdown(proposals))

    return json_path


def format_proposals_markdown(proposals: list[dict[str, Any]]) -> str:
    """Cavecrew-style compact bullet digest — no marketing fluff."""
    if not proposals:
        return "_No actionable proposals today._"
    lines: list[str] = []
    for p in proposals:
        ap = p.get("actionable_proposal", {}) or {}
        lines.append(f"### {p.get('title', '(untitled)')}")
        lines.append(f"- source: {p.get('source_url', '')}")
        lines.append(f"- category: {p.get('category', '?')} | relevance: {p.get('relevance', '?')} | impact/feasibility: {p.get('impact_feasibility', '?')}")
        lines.append(f"- proposal: {ap.get('summary', '')}")
        lines.append(f"- target: `{ap.get('target_module', '')}`")
        for step in ap.get("steps", []):
            lines.append(f"  - {step}")
        lines.append(f"- benefit: {ap.get('estimated_benefit', '')}")
        lines.append("")
    return "\n".join(lines).strip()


def generate_proposals(
    github_signal: dict[str, Any],
    community_signal: dict[str, Any],
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Main entry point. Never raises — returns [] on any failure."""
    try:
        candidates = _extract_candidates(github_signal, community_signal)
        new_candidates = filter_new(candidates)
        if not new_candidates:
            log.info("evaluator: no new candidates to evaluate")
            return []
        new_candidates = new_candidates[:EVALUATOR_MAX_CANDIDATES]

        new_candidates = _enrich_with_deep_context(new_candidates)
        toon_payload, _baseline, savings = _build_llm_payload(new_candidates)
        user_msg = (
            "Zde jsou dnesni kandidati (TOON format) k architektonickemu vyhodnoceni. "
            "Vrat JSON pole presne dle schematu ze system promptu:\n\n" + toon_payload
        )

        model_chain = (model,) if model else OPENROUTER_MODEL_CHAIN
        raw: str | None = None
        for m in model_chain:
            raw = _call_openrouter_json(user_msg, m)
            if raw:
                break

        if not raw:
            log.warning("evaluator: all models failed to produce proposals")
            return []

        proposals = _parse_proposals_json(raw)
        if proposals is None:
            log.warning("evaluator: could not parse LLM JSON output")
            return []

        log.info("evaluator: %d proposals generated (%s)", len(proposals), format_savings_footer(savings).strip())
        mark_seen(new_candidates)
        _save_proposals(proposals)
        return proposals
    except Exception as exc:  # noqa: BLE001 — evaluator must never break the main digest
        log.warning("evaluator: unexpected failure: %s", exc)
        return []
