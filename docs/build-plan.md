# Build Plan

Solo build, 2026-10-09 to 2026-10-27. Build only what appears in the 2-minute demo first; stretch goals come last.

## Decisions already made

| Decision | Value |
| --- | --- |
| Project name | Pitch Ledger |
| Tracking rate | 1 sample per second |
| Matches to generate | 6: one per planted scenario (late_collapse, high_press_switch, red_card, hot_streak, none) + one extra baseline |
| Demo languages | English, Spanish, Filipino |
| Names | One fictional league, clubs and players, generated once and kept fixed (committed as a roster file) |
| Storage | Eventhouse for events; Cosmos DB for the ledger |
| Repo | Public, MIT license, personal GitHub account |
| Pass end point | A pass row stores its intended target as end_x/end_y, so difficulty recomputes exactly from the row; the next event shows where an incomplete pass was cut out or left the pitch |
| Scenario pairings and seeds (home vs away) | M0001 none T01 vs T02 (4211); M0002 late_collapse T01 vs T02 (4212); M0003 high_press_switch T06 vs T02 (4213); M0004 red_card T03 vs T04 (4213); M0005 hot_streak T05 vs T06 (4212); M0006 none T02 vs T01 (4212). Selection rule in `scenarios/matches.yaml` |
| Momentum detection | Evidence accumulation (CUSUM) on pass outcomes vs the difficulty model, pressured passes and possession endings, not the 5-minute 20-point rule (which fired ~11 times per baseline match); control score kept for explanation |
| Planted effect size | Dramatic effects, 15-minute detection limit (37–40 of 40 seeds detected per scenario; momentum false alerts in 1 of 80 baseline matches) |

## Phases

### Phase 1 — Foundations and Match Lab (Oct 9–12) · local only, no Azure cost

1. Scaffold the repo per `CLAUDE.md` (pyproject with `uv`, ruff, pytest, CI workflow that runs lint + tests).
2. `schema/`: Pydantic models and enums for match, team, player, event (core + context + details), player_tracking, insight, overlay, scenario.
3. `validation/`: row, sequence and match rules from the schema doc, returning structured reject/flag results.
4. `matchlab/roster.py`: fixed fictional league (at least 4 clubs, full squads), committed as data.
5. `matchlab/generator.py`: possession-based simulation producing events with coordinates, timing, pressure, outcomes driven by the pass-difficulty and xG models, team style profiles, score and possession bookkeeping, substitutions and cards.
6. `matchlab/tracking.py`: 1 Hz player positions and speeds consistent with events.
7. CLI: `generate`, `validate`, `realism`. Outputs JSONL (live feed format) and Parquet/CSV.
8. Tests: schema round-trips, every validation rule has a passing and failing case, the baseline match passes validation and lands inside all realism bands, generation is reproducible from the seed.

**Done when:** `pitch-ledger generate --scenario none` produces a valid, realistic match twice with identical output for the same seed.

### Phase 2 — Engine, scenarios and detector (Oct 12–14)

1. `engine/`: derived metrics, rolling 5-minute windows, PPDA, tempo, control and chaos scores, player physical metrics, milestones and speed alerts.
2. Planted scenarios as YAML; generator applies their effects.
3. `engine/detector.py`: triggers with evidence event IDs.
4. Scenario tests: each scenario is detected within its time limit; the baseline produces no false momentum alerts.
5. **Azure setup in parallel:** personal subscription, budget alerts, resource group, Foundry project with two model deployments (confirm region availability).
6. **Latency spike:** Fabric trial, Eventstream → local Python consumer; time the hop. Decide Fabric vs Event Hubs-direct for the live path.

**Done when:** all 5 scenarios pass detection tests locally.

### Phase 3 — Agents and ledger (Oct 15–19)

1. `agents/model_manager.py` (single entry point to Foundry; token caps; retries; fallback).
2. Stats MCP server with read-only tools: `get_events`, `get_team_state`, `get_player_stats`, `recompute_metric`.
3. Explainer, Auditor (maker-checker, max 2 rounds), Narrator (handoff at breaks), Personalizer and Translator (concurrent), Overlay builder (code).
4. Ledger repository: in-memory for tests, Cosmos DB adapter for cloud.
5. Service Bus trigger queue with retry and dead letter (in-memory adapter locally).
6. **Planted error test:** inject a wrong number into an Explainer draft and assert the Auditor blocks or corrects it. This is the demo's key moment.
7. Evaluation script: detection rate, false alerts, factual accuracy across all scenario matches; results pushed to Foundry evaluations.

**Done when:** replaying a scenario match locally produces audited insights and overlays in three languages and five audience modes, with the evaluation scorecard.

### Phase 4 — Studio app and deployment (Oct 20–23)

1. FastAPI `/api/v1`: matches, insights, overlays, approvals, viewer preferences.
2. Next.js Studio app: producer console (Entra sign-in, approve/block queue with evidence drill-down) and fan view (match timeline, overlay layer, mode / club / player / metric / language selectors).
3. Web PubSub live push.
4. Bicep for all Azure resources; GitHub Actions build to GHCR and deploy to Container Apps.
5. RTI dashboard (control room) and Activator ops alerts in Fabric.
6. Managed identities, key auth disabled, content-safety policies, Application Insights and Foundry tracing.

**Done when:** the public link replays a match end to end in Azure.

### Phase 5 — Polish, video, submission (Oct 24–27)

1. README: problem, architecture diagram, how to run locally, how to test the live demo, enterprise controls, production path, cost, evaluation scorecard, screenshots.
2. Record the demo video (under 2 minutes, no trademarks, no copyrighted music).
3. Update the project page description to match what is actually built.
4. Testing instructions for judges (public link; credentials only if needed).
5. Oct 26 buffer; submit early on Oct 27.
6. Early November: upgrade the Azure subscription to pay-as-you-go before the free credit expires so the demo stays live until Nov 20.

## Demo script (target 1:50)

1. 0:00 Problem in one line; Pitch Ledger in one line.
2. 0:10 Synthetic match running live; control-room dashboard.
3. 0:25 Planted collapse at 60': momentum shift detected; Explainer's draft appears with evidence.
4. 0:45 Auditor catches an over-reaching number and blocks it; corrected insight passes.
5. 1:00 Producer approves; overlay fires on screen.
6. 1:10 Same moment for an analyst (numbers) and a casual fan (one sentence), in English and Filipino or Spanish.
7. 1:30 Full-time recap with evidence trail; trace and evaluation scorecard.
8. 1:45 Architecture slide and close.

## Stretch goals (only after Phase 4 is done)

- Lakehouse medallion history.
- Fabric data agent "ask the match" connected to a Foundry agent.
- Orchestrator as a Foundry hosted agent.
- SPADL export.
