# ⚡ Tech-Radar Agent

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Zero-Cost Infrastructure](https://img.shields.io/badge/API%20Cost-$0.00-brightgreen.svg)]()
[![OpenRouter Free Tier](https://img.shields.io/badge/LLM-OpenRouter%20Free-blueviolet.svg)](https://openrouter.ai/)
[![Telegram Forum Topics](https://img.shields.io/badge/Platform-Telegram%20Topics-2CA5E0.svg)](https://telegram.org/)

**The zero-cost autonomous AI intelligence dispatcher.** It scouts GitHub releases, trending open-source repositories, Hacker News, and community signals every morning, synthesizes high-leverage developments using free-tier LLMs, and delivers an executive brief directly into your dedicated Telegram Forum Topic. No marketing fluff, no PR hype — strictly actionable technical signal.

---

| Capability | What's Covered |
| :--- | :--- |
| **🛠 Token & Infra Hacks** | Tracking releases in LLM serving runtimes (`vLLM`, `LiteLLM`, `Unsloth`, `llama.cpp`, `Ollama`). Context compression, quantization, caching, and throughput speedups. |
| **🤖 Agentic Frameworks** | Autonomous agent design patterns, tool-calling pipelines, RAG architectures, reflective workflows, and multi-agent coordination. |
| **🚀 Trending Repositories** | Curating breakout developer tools and foundational models from GitHub daily charts with direct source links. |
| **🌐 Scientific Signal** | Direct papers, benchmark shifts, and open weight releases from top research labs without corporate filler. |
| **⏳ 14-Day Deduplication** | Local sliding cache window ensures you never see the same release note or thread twice. |
| **💬 Telegram Forum Native** | Native `message_thread_id` dispatching directly into your topic channel (e.g. `#Tech-Radar`) with smart paragraph chunking. |

---

## 🏗 System Architecture

```
                                 [ Data Sources ]
                                        │
           ┌────────────────────────────┼────────────────────────────┐
           ▼                            ▼                            ▼
  [ GitHub Releases ]          [ GitHub Trending ]         [ Hacker News Algolia ]
 (vLLM, Unsloth, LiteLLM)       (AI / Dev Tools)              (Points > 30)
           │                            │                            │
           └────────────────────────────┼────────────────────────────┘
                                        │
                                        ▼
                           [ Core Deduplication Engine ]
                           (14-day sliding history cache)
                                        │
                                        ▼
                           [ LLM Synthesis Pipeline ]
                         (OpenRouter Free Tier: MiniMax 3)
                                        │
                                        ▼
                         [ Telegram Topic Dispatcher ]
                     (Supergroup Thread ID + Smart Chunking)
```

---

## ⚡ Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/Molyn597/tech-radar-agent.git
cd tech-radar-agent

# 2. Setup virtual environment
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy environment configuration
cp .env.example .env

# 5. Run simulation (Dry-Run: inspects signal without sending to Telegram)
python main.py --dry-run
```

---

## ⚙️ Configuration Reference

All settings can be configured via `.env` or system environment variables:

| Variable | Description | Required | Example |
| :--- | :--- | :---: | :--- |
| `TELEGRAM_BOT_TOKEN` | Bot API token from [@BotFather](https://t.me/botfather) | Yes | `123456789:ABCdefGhI...` |
| `TELEGRAM_CHAT_ID` | Telegram chat ID (group or supergroup) | Yes | `-1001234567890` |
| `TELEGRAM_THREAD_ID` | Specific forum topic / thread ID | Optional | `3` |
| `OPENROUTER_API_KEY` | OpenRouter API Key (supports free models) | Yes | `sk-or-v1-...` |

---

## 🕹 CLI Usage

| Command | Action |
| :--- | :--- |
| `python main.py --dry-run` | Scrapes sources, performs dedup and LLM synthesis, prints digest to terminal. |
| `python main.py` | Full production run: scrapes, synthesizes, dispatches to Telegram, updates history. |
| `python main.py --limit 15` | Caps max items fetched per source to 15. |
| `python main.py --json` | Dumps raw scraped signal to stdout for inspection and debugging. |

---

## ⏰ Automated Scheduling (24/7 Production)

### Linux (Systemd / Cron on Ubuntu VPS)
```cron
# Run daily at 08:15 AM
15 8 * * * /home/ubuntu/tech-radar-agent/.venv/bin/python /home/ubuntu/tech-radar-agent/main.py >> /home/ubuntu/tech-radar-agent/radar.log 2>&1
```

### Windows (Task Scheduler)
Create a daily task running `python.exe` with argument `main.py` in the project directory at 08:15.

---

## 🛡️ Privacy & Security

- **Zero Plaintext Secrets**: Tokens and API keys load securely from environment variables, Windows `HKCU\Environment` registry, or `.env` (git-ignored).
- **Zero Third-Party Tracking**: Dedup state is kept completely locally in `data/history.json`.

---

## 📄 License
MIT License — see [LICENSE](LICENSE) for details. Built by [Molyn597](https://github.com/Molyn597).
