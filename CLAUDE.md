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
- `docs/azure-setup.md` — Azure provisioning log (Foundry, service principal, roles), settings, verification and lessons learned.
- `docs/requirements-map.md` — every point of the challenge brief mapped to what is built (implemented, partial, not implemented, beyond the brief) and the gaps to close. Update a row when its status changes.

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
| Live data | **(override)** Azure Event Hubs for the live feed (feed adapter); Azure Data Explorer free cluster as the event store with an ADX dashboard for the control room; ops alerts in the producer console. Fabric RTI (Eventstream, Eventhouse, Real-Time Dashboard, Activator) is a future upgrade: the trial is not available |
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
- **Every external service gets an adapter** in an infrastructure module (Event Hubs/Eventstream, Azure Data Explorer, Service Bus, Cosmos DB, Web PubSub). Domain code never imports a vendor SDK.
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
    feed/                    live feed interfaces, file and in-memory adapters, stream and listen
    eventstore/              event-store tables, row builders, file adapter (ADX adapter in infra_adapters)
    engine/                  derived metrics, rolling windows, control/chaos, detector
    mcp_server/              read-only stats tools
    agents/                  model_manager, explainer, auditor, narrator, personalizer, translator, orchestrator, overlay builder
    ledger/                  insight ledger repository (Cosmos + in-memory)
    history/                 season history: match records, file/memory stores, league table (Cosmos in infra_adapters)
    api/                     FastAPI app (/api/v1)
    infra_adapters/          event hubs (also Fabric Eventstream endpoints), azure data explorer, service bus, cosmos, web pubsub
  studio/                    Next.js app
  tests/                     mirrors src/
  data/                      generated outputs (gitignored except small samples/)
  infra/                     Bicep for Azure resources; adx/ table script and dashboard tile queries
  .github/workflows/         CI and deploy
```

## Commands (keep this list current as they are added)

```
uv sync                                  # install
uv run pytest                            # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy                              # strict type check of src/pitch_ledger
uv run pitch-ledger generate --scenario none --seed 4211 --out data/M0001
uv run pitch-ledger generate --scenario late_collapse --seed 4212 --out data/M0002   # planted scenario
uv run pitch-ledger generate --seed 7 --home T03 --away T05 --out data/M0009   # other clubs (T01-T06)
uv run pitch-ledger generate-all --out data   # the six demo matches in scenarios/matches.yaml
uv run pitch-ledger validate data/M0001  # every rule; exit 1 on any reject (--no-realism, --no-tracking)
uv run pitch-ledger realism data/M0001   # compare against realism bands (--strict: exit 1 if out)
uv run pitch-ledger detect data/M0002    # triggers.jsonl + scenario check; exit 1 on a miss or false alert (--all)
uv run pitch-ledger mcp data/M0002       # read-only stats tools as an MCP server (stdio)
uv run pitch-ledger replay data/M0002 --approve --inject-error   # agents -> data/M0002/ledger (--provider foundry, --only, --budget, --local-stats, --history file|cosmos|none: season facts from earlier matches)
uv run pitch-ledger evaluate              # scorecard -> data/scorecard.json; exit 1 if a gate fails (--match M0002, --sweep N, --store adx: keep the run in EvalRuns)
uv run pitch-ledger history build --data data   # a match record per finished match -> history store (--history file|cosmos, --store adx)
uv run pitch-ledger history show --data data    # the season's results and league table from the records
uv run pitch-ledger foundry-eval --ledger cosmos   # every aired insight -> Foundry evaluations: code checks + groundedness, coherence, fluency (--match, --no-judges, --safety, --dry-run)
uv run pitch-ledger check-models          # model settings, Entra ID sign-in, one tiny call per deployment; exit 1 on a FAIL
uv run pitch-ledger check-models --safety # also a prompt-injection probe the content filter must block
uv run pitch-ledger check-tracing         # tracing settings, Entra ID sign-in, one test span to Application Insights
uv run pitch-ledger listen                # read the live feed into data/live/<match>/, report latency (--feed, --idle, --from-start)
uv run pitch-ledger stream data/M0002 --speed 10   # publish a match to the live feed at 10x pace (--start-min, --minutes, --feed, --fault-at MIN: demo ops alerts)
uv run pitch-ledger store-schema > infra/adx/tables.kql   # KQL script for the event-store tables (keep the file in sync)
uv run pitch-ledger store data/M0002     # write a match to the event store: data/store/*.jsonl, or ADX with --store adx
uv run pitch-ledger check-store           # ADX settings, sign-in, roles, tables, one test write; exit 1 on a FAIL
uv run pitch-ledger check-ledger          # Cosmos DB settings, sign-in, one document written, read, deleted
uv run pitch-ledger check-queue           # Service Bus settings, sign-in, one probe trigger sent and received
uv run pitch-ledger check-push            # Web PubSub settings, sign-in (signed client URL), one test signal
PITCH_LEDGER_API_AUTH=dev uv run pitch-ledger api   # /api/v1 on 127.0.0.1:8000 (docs at /api/v1/docs); dev = local producer
uv run pitch-ledger live --store adx --approve   # live service: feed -> engine -> queue -> agents -> ledger (--feed, --queue, --ledger, --provider, --inject-error, --fresh)
cd studio && npm install && npm run dev  # Studio on http://localhost:3000 (homepage, matches, Match Centre, producer console); needs the API
cd studio && npx next typegen && npx tsc --noEmit && npm run lint && npm run build   # Studio checks (CI job "studio")
uv run pitch-ledger replay data/M0002 --approve --ledger cosmos --queue servicebus   # cloud ledger and queue
docker build -t pitch-ledger . && docker build -t pitch-ledger-studio studio   # the two images (Deploy workflow builds and pushes them)
az bicep build --file infra/main.bicep && az bicep lint --file infra/main.bicep   # infra check (CI job "infra")
az deployment group create -g <resource-group> --parameters infra/main.bicepparam   # deploy by hand (normally the Deploy workflow)
```

Match directory: `match.json` (match, teams, players), `events.jsonl` / `tracking.jsonl` (live-feed format), `events.parquet` / `tracking.parquet` (same rows; `details` as a JSON string), `triggers.jsonl` (after `detect` or `replay`), `ledger/` (after `replay`: `insights.jsonl`, `audits.jsonl`, `overlays.jsonl`, `stories.jsonl`, `triggers.jsonl`, `alerts.jsonl`, `run.json`). Season history: `data/history/records.jsonl` (one match record per line). Scenario definitions live in `scenarios/<scenario_id>.yaml`; `scenarios/matches.yaml` gives the demo matches their seeds and weekly kick-offs.

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
