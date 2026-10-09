# CLAUDE.md — Pitch Ledger

Read this first in every session. It is the source of truth for how to work in this repo.

## What this project is

**Pitch Ledger** is a solo entry to the Microsoft "Inside the Game" developer hackathon, challenge: *The Synthetic Match Insights Engine for Premier League Studio*. It turns **synthetic** football events into real-time, explainable, personalized match intelligence for studio, broadcast and streaming.

Pipeline: **ingest → interpret → explain → render → personalize**.

Core idea: an **insight ledger**. Code computes every number; AI agents only explain them; an Auditor agent recomputes every figure against cited evidence events before anything airs; a producer approves; then insights are personalized per audience and language.

Target prize category: **Best Enterprise Solution** (plus Grand Prize). Strong multi-agent and Foundry usage matter for every judging criterion.

## Deadlines

- Submissions close **2026-10-27** (submit early that day; confirm the cutoff time zone).
- Judging runs to **2026-11-20**; the deployed demo must stay live and free to test until then.

## Key documents (read before changing behaviour)

- `docs/event-schema.md` — data contract: events, tracking, derived-metric formulas, insight and overlay objects, validation rules, realism targets, planted scenarios. **Code must follow it.** If code and doc disagree, raise it; update the doc in the same change if the change is agreed.
- `docs/architecture.md` — final architecture, components, agents, enterprise controls, cost plan.
- `docs/build-plan.md` — phases, order of work, definition of done.

## Non-negotiables

1. **Synthetic data only.** Never download, copy or calibrate from real or provider match data (StatsBomb, Opta, etc.). Calibrate only to the published aggregate benchmarks in the schema doc.
2. **All names fictional.** No real clubs, players, leagues, logos or crests. Never use "Premier League" or other trademarks in product names, UI, sample data or demo assets.
3. **No numbers from a language model.** Every statistic is computed in deterministic Python and reached by agents through read-only tools. Agents explain; they never calculate or invent figures.
4. **Every insight cites evidence event IDs** and passes the Auditor before air.
5. **Personal resources only.** Personal GitHub account and personal Azure subscription in the personal Default Directory. Never the former employer's tenant, subscription, credits or code.
6. **Original work.** Reference architectures and sample repos are for patterns only; do not copy their code bases.
7. **No secrets in the repo.** Use environment variables locally and managed identities / Key Vault in Azure. Keep `.env` gitignored; commit `.env.example` only.
8. **Cost discipline.** No hackathon credits. Prefer free tiers, scale to zero, cap tokens. Flag anything that could add recurring cost before adding it.

## Stack

Defaults follow my personal stack, with challenge-driven overrides marked **(override)**.

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 for all backend, generator, engine and agents |
| Schemas | Pydantic v2 for every model; `pydantic-settings` for config |
| API | FastAPI + Uvicorn, routes under `/api/v1` |
| Agents | **(override)** Microsoft Agent Framework (Python), code-first orchestration |
| Models | **(override)** Microsoft Foundry deployments: one stronger model (Explainer, Auditor, Narrator), one small fast model (Personalizer, Translator). Model names live in one config place only |
| Tools for agents | **(override)** MCP server exposing read-only stats tools |
| Live data | **(override)** Fabric Real-Time Intelligence: Eventstream, Eventhouse, RTI dashboard, Activator |
| Queue | **(override)** Azure Service Bus (trigger queue, retries, dead letter) |
| Store | **(override)** Azure Cosmos DB (free tier) as the insight ledger, not Postgres |
| Push | **(override)** Azure Web PubSub (Free tier) |
| Frontend | Next.js App Router, TypeScript strict, Tailwind + shadcn/ui, Lucide icons (Studio app: producer console + fan view) |
| Hosting | **(override)** Azure Container Apps for every service; images on GitHub Container Registry |
| CI/CD | GitHub Actions |
| Tooling | `uv` for Python env and deps, `ruff` (lint + format), `pytest`, `mypy` on core packages |

Architecture rules:
- Organize code **by domain** (matchlab, engine, ledger, agents), not by technical type.
- `core → domain → infrastructure`; inner layers never import outer ones.
- **All model calls go through one model-manager module** (`agents/model_manager.py`). Nothing else imports a provider SDK.
- **Every external service gets an adapter** in an infrastructure module (Eventstream/Event Hubs, Service Bus, Cosmos DB, Web PubSub). Domain code never imports a vendor SDK.
- **Local-first:** every service must run locally with file-based replay and in-memory or emulator adapters, no cloud needed. Cloud adapters are swapped in by config.

## Repo layout (target)

```
pitch-ledger/
  CLAUDE.md
  README.md
  pyproject.toml
  .env.example
  docs/                      event-schema.md, architecture.md, build-plan.md
  scenarios/                 one YAML per planted scenario
  src/pitch_ledger/
    schema/                  Pydantic models + enums for every table and output object
    validation/              row, sequence, match and insight rules
    matchlab/                roster, generator, scenarios, tracking, replay CLI
    engine/                  derived metrics, rolling windows, control/chaos, detector
    mcp_server/              read-only stats tools
    agents/                  model_manager, explainer, auditor, narrator, personalizer, translator, orchestrator, overlay builder
    ledger/                  insight ledger repository (Cosmos + in-memory)
    api/                     FastAPI app (/api/v1)
    infra_adapters/          eventstream, service bus, cosmos, web pubsub
  studio/                    Next.js app
  tests/                     mirrors src/
  data/                      generated outputs (gitignored except small samples/)
  infra/                     Bicep for Azure resources
  .github/workflows/         CI and deploy
```

## Commands (keep this list current as they are added)

```
uv sync                                  # install
uv run pytest                            # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy                              # strict type check of src/pitch_ledger
uv run pitch-ledger generate --scenario none --seed 4211 --out data/M0001
uv run pitch-ledger validate data/M0001
uv run pitch-ledger realism data/M0001   # compare against realism bands
```

## Definition of done for any change

- Tests added or updated; `pytest` and `ruff` pass.
- Follows the schema doc; validation rules still pass on generated matches.
- No real names, trademarks or secrets introduced.
- Docs updated when behaviour or commands change.
- Small, focused commits with clear messages.

## How to work with me (the owner)

- I'm a data scientist with an accounting background, strong in Python, SQL and analytics, newer to football and to Azure app infrastructure. Explain football and Azure choices briefly when they matter.
- Keep the demo story in mind: a planted momentum shift is detected, explained, an over-reaching stat is **caught and blocked by the Auditor**, the producer approves, and the same moment appears for an analyst and a casual fan in two languages.
- Prefer the simplest working version first; stretch goals (lakehouse, Fabric data agent, hosted agents) only after the core demo works end to end.
- Ask before adding new paid services, new frameworks, or anything that changes the architecture doc.
