<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
  <img alt="Augura" src="docs/assets/logo-light.svg" width="72">
</picture>

# Augura

**Ad creatives expire. The judgment behind them shouldn't.**

A local-first creative intelligence system for mobile-game UA teams.<br/>
It decomposes every creative into structure, links them into a graph, and turns every human ruling into a compounding team asset.

[![CI](https://github.com/augura-os/augura/actions/workflows/ci.yml/badge.svg)](https://github.com/augura-os/augura/actions/workflows/ci.yml)

**English | [简体中文](README.zh-CN.md)**

</div>

## What is Augura?

Every UA team shares the same quiet pain: only the creative planner who designed a hit knows why it hit — and when they leave, the experience leaves with them. Creatives sit in cloud drives, data sits in spreadsheets, and judgment calls like "is this the same concept as last month's?" get re-made from scratch, every single week.

Augura systematizes this. Upload a creative and AI decomposes it into hook, conflict, gameplay and reward. Decomposed creatives cluster into a five-layer graph: DNA → Creative → Variant → Asset. From then on, every human ruling — merge or split, did this iteration work — is remembered. The next time the same situation shows up, the system brings a suggestion; you just confirm.

Think of it as local-first creative intelligence infrastructure: your data stays on your machine by default (frame samples go to the model provider you configure during AI analysis), judgment stays with you, and the repetitive work goes to the system.

## Features

- **AI creative analysis** — videos are frame-sampled and read by a vision model into structured JSON (hook / conflict / gameplay / reward / characters / emotion / tags). Low-confidence results route to human review — the AI never gets to guess silently.
- **Creative graph** — a five-layer knowledge graph (Neo4j) visualizes your entire creative world. Same filename with KS_EN and KS_KR market tags? The system spots two market versions of one creative and waits for your one-word ruling.
- **Merge guard** — pairs you once ruled "not the same creative" are remembered forever. Anyone trying to merge them must write down why. You make each judgment once — never twice.
- **Evolution tracking** — every variant records what it derived from and which factor changed (language / aspect ratio / intro sticker). When performance data flows back, you verdict the iteration; when a direction is exhausted, the system says so.
- **Review inbox** — merge candidates, unassigned creatives, observation-pair closures, each pre-adjudicated by LLM self-consistency votes with reasons attached. And there's a brake on AI mistakes: if the override rate crosses the line, auto-judging degrades to suggestion-only.
- **Configurable metrics** — CPP red line, ROAS green line, whether D3 ROAS or D1 retention participate in verdicts: tune it all in Settings, no code changes.
- **Data sovereignty** — creatives, performance rows and analyses live on your own machine / server / NAS (local data volumes). The Creative Genome Program is whitelist-only (creative names hashed), one-click opt-out, fully auditable.

## Quick Start

**Prerequisite**: [Docker Desktop](https://www.docker.com/products/docker-desktop/).

**macOS / Linux** — one line, no clone needed:

```bash
curl -fsSL https://raw.githubusercontent.com/augura-os/augura/main/scripts/install.sh | bash
```

On macOS you can also download the repo and double-click `install.command`. On **Windows**, double-click `install.bat`. All of them are idempotent — re-running updates and restarts.

Manually, from a checkout:

```bash
cp .env.example .env
docker compose up -d          # release: pull prebuilt images from ghcr (~2-3 min)
docker compose up --build -d  # dev/private: build locally (first run ~20 min)
```

| Service | URL |
| --- | --- |
| Web (home = Creative Graph) | http://localhost:3000 |
| API | http://localhost:8000/docs |
| Neo4j Browser | http://localhost:7474 (random password generated on first install — see .env) |
| MinIO Console | http://localhost:9001 (same) |

> **Deployment boundary**: Augura is a **single-user local tool** — no auth, no multi-tenancy, all ports bind to 127.0.0.1 by default. **Do not expose it directly to the public internet**; team/server deployments need your own reverse proxy + auth layer.

## Getting Going

### 1. Set your AI key

Open **Settings** and paste a key from any OpenAI-compatible provider. The preset buttons fill in the matching Base URL and models for you:

| Key source | Base URL | Vision model | Embedding |
| --- | --- | --- | --- |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` | `text-embedding-3-small` |
| Kimi 开放平台 (platform.moonshot.cn) | `https://api.moonshot.cn/v1` | `kimi-k2.5` | — (local text similarity fallback) |
| Kimi 编程套餐 (kimi.com, `sk-kimi-…` keys) | `https://api.kimi.com/coding/v1` | `kimi-for-coding` | — (同上) |

The two kinds of Kimi keys are **not interchangeable** — a `sk-kimi-` coding-plan key returns 401 on `api.moonshot.cn`, and vice versa. A Base URL missing its `/v1` path fails analysis with a 404; Settings warns before saving such a URL. Self-hosted / local endpoints (Ollama-compatible, etc.) work too — just fill in your own Base URL, and your frames never leave the machine. Nothing is called until you upload something.

### 2. Upload a batch

Drag mp4 / png / xlsx (Facebook exports) into **Upload**. Each video takes a few minutes to analyze — grab a coffee and watch the graph grow.

### 3. Read the graph, clear the inbox

By the time you're back, suspected duplicates are already linked as candidates. Open the **inbox**: every item carries an LLM suggestion with its reasoning. Merge, split, assign — one click each.

### 4. Feed performance back

Upload the week's delivery Excel and spend, payers and ROAS match back to creatives automatically. The **Daily Brief** tells you what to scale, what to iterate and what to kill — with the numbers behind every call.

That's it. Your creative assets have started compounding. 🎉

## Multiple projects?

One Augura instance = one project: the graph, asset library, DNAs and inbox are all instance-global. To manage multiple games/projects, **run a separate instance per project** (fully isolated data):

```bash
git clone <repo> augura-game-a
git clone <repo> augura-game-b
```

In `augura-game-b/`, add a `docker-compose.override.yml` to shift the host ports (container-to-container traffic uses the internal network and is unaffected; `!override` replaces the port list wholesale and needs Docker Compose v2.24+):

```yaml
services:
  postgres:
    ports: !override
      - "127.0.0.1:5434:5432"
  neo4j:
    ports: !override
      - "127.0.0.1:7475:7474"
      - "127.0.0.1:7688:7687"
  minio:
    ports: !override
      - "127.0.0.1:9002:9000"
      - "127.0.0.1:9003:9001"
  api:
    ports: !override
      - "127.0.0.1:8001:8000"
  web:
    ports: !override
      - "127.0.0.1:3001:80"
```

Run `docker compose up -d` in each directory; project B lives at http://localhost:3001. AI keys, market prefixes etc. are configured per instance on its own Settings page. Caveats: no cross-project analysis, and each instance runs its own Postgres/Neo4j/MinIO (memory ×N). Single-instance multi-project support (a project switcher) is on the roadmap — the schema already reserves `project_id`.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
│  React Web   │────>│  FastAPI     │────>│   PostgreSQL     │
│ (graph/inbox)│<────│ (rule engine)│<────│ (structured data)│
└──────────────┘     └──────┬───────┘     └──────────────────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
        ┌─────┴─────┐ ┌─────┴─────┐ ┌─────┴─────┐
        │   Neo4j   │ │   MinIO   │ │ LLM provider│
        │  (graph)  │ │ (files)   │ │(analysis/  │
        └───────────┘ └───────────┘ │ judging)   │
                                    └───────────┘
```

| Layer | Stack |
|------|--------|
| Frontend | React + TypeScript + Vite + React Flow |
| Backend | FastAPI + SQLAlchemy + Alembic (Python 3.12) |
| Storage | PostgreSQL 16 · Neo4j 5 · MinIO |
| AI | Any OpenAI-compatible endpoint (OpenAI / Kimi…), vision-model analysis + self-consistency judging |

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) (trunk-based + PR + CI).

```bash
cd apps/web && npm install && npm run dev   # hot reload, proxied to :8000
cd apps/api && pytest                        # backend tests
```

## Data & Privacy

See [PRIVACY.md](PRIVACY.md). The precise version: **raw creatives and performance details stay on your machine by default; during AI analysis, frame samples and structured data go to the model provider you configure in Settings** (any OpenAI-compatible endpoint — the destination is your choice). We want your judgment, not your assets.

What the Creative Genome Program sends (on by default, one-click opt-out in Settings):

| Event | Fields | Purpose |
| --- | --- | --- |
| Feature click | feature name, time bucket | usage stats |
| Correction | field, change direction (creative names hashed) | model training |
| Aggregates | genre, metric bucket, count | industry benchmarks |

Operational data (always on — errors and version only, for compatibility support):

| Event | Fields | Purpose |
| --- | --- | --- |
| Session start | app version, OS family, time bucket | compatibility |
| Error | error code, stack signature, time bucket | stability fixes |

## Why "Augura"?

Design production has Figma. Code production has Git. Media buying has the ad platforms. But *how a creative actually evolves* — that process never had a system of record.

Augura aims to be the operating system for creative production: creatives are processes, the graph is the file system, and every human ruling is experience written into the kernel. One buyer's judgment is finite; a team that accumulates isn't.

## License

[Modified Apache 2.0](LICENSE) (Apache 2.0 incorporated + additional terms), attribution in [NOTICE](NOTICE):

- Normal use, modification and internal deployment: fully free.
- Offering the software as a hosted service / SaaS to third parties requires a commercial license.
- Don't remove the logo or copyright notices from the UI; backend-only usage is exempt.
