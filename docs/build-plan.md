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
| Storage | Cosmos DB for the ledger (system of record); Azure Data Explorer free cluster as the event store (raw events, engine metrics, triggers, ops alerts) for KQL and the dashboard |
| Control room and ops alerts | ADX dashboard for the control room; ops alerts (feed gaps and lag, validation rejects, blocked insights, dead letters, model fallbacks) in the producer console. Fabric (Eventhouse, RTI dashboard, Activator) is a documented future upgrade |
| Live feed | Azure Event Hubs (Basic) behind the feed adapter (`PITCH_LEDGER_FEED=eventhubs`), because the Fabric trial is not available. Fabric Eventstream can take over later through its custom endpoint (settings only) or by reading the hub |
| Repo | Public, MIT license, personal GitHub account |
| Pass end point | A pass row stores its intended target as end_x/end_y, so difficulty recomputes exactly from the row; the next event shows where an incomplete pass was cut out or left the pitch |
| Scenario pairings and seeds (home vs away) | M0001 none T01 vs T02 (4211); M0002 late_collapse T01 vs T02 (4212); M0003 high_press_switch T06 vs T02 (4213); M0004 red_card T03 vs T04 (4213); M0005 hot_streak T05 vs T06 (4212); M0006 none T02 vs T01 (4212). Selection rule in `scenarios/matches.yaml` |
| Momentum detection | Evidence accumulation (CUSUM) on pass outcomes vs the difficulty model, pressured passes and possession endings, not the 5-minute 20-point rule (which fired ~11 times per baseline match); control score kept for explanation |
| Planted effect size | Dramatic effects, 15-minute detection limit (37–40 of 40 seeds detected per scenario; momentum false alerts in 1 of 80 baseline matches) |
| Agent flow by insight type | Momentum shift, key moment, player form and recap: Explainer (Narrator for recaps) ⇄ Auditor, producer approval, five audience modes in three languages. Speed alerts: template copy, code audit, auto-approved. Milestones and pass quality: stored in the ledger, not processed |
| Player form rate | Fires in about 8% of baseline matches; accepted |
| Planted error | `--inject-error` doubles a count in the first draft of the first momentum insight (M0002: 16 turnovers written as 32); the Auditor must reject it and the corrected draft airs |
| Models | Offline templates by default (zero cost, also the fallback); live Foundry calls once the project settings exist, capped by a per-run call budget |
| Cloud adapters | Cosmos DB ledger and Service Bus queue adapters move to Phase 4 with the Bicep; Phase 3 builds the interfaces with in-memory and local-file adapters |

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
6. ~~**Latency spike:** Fabric trial, Eventstream → local Python consumer; time the hop.~~ Resolved 2026-10-09: the Fabric trial is not available, so the live feed is Azure Event Hubs. Measured from a cloud session through its proxy: no loss, p50 2.5–3.3 s, p95 4.6–7.3 s (pessimistic; re-measure from Container Apps).

**Done when:** all 5 scenarios pass detection tests locally.

### Phase 3 — Agents and ledger (Oct 15–19)

1. `agents/model_manager.py` (single entry point to Foundry; token caps; retries; fallback).
2. Stats MCP server with read-only tools: `get_events`, `get_team_state`, `get_player_stats`, `recompute_metric`.
3. Explainer, Auditor (maker-checker, max 2 rounds), Narrator (handoff at breaks), Personalizer and Translator (concurrent), Overlay builder (code).
4. Ledger repository interface: in-memory for tests, local JSONL files for replays (Cosmos DB adapter in Phase 4).
5. Trigger queue interface with retry and dead letter: in-memory adapter (Service Bus adapter in Phase 4).
6. **Planted error test:** inject a wrong number into an Explainer draft and assert the Auditor blocks or corrects it. This is the demo's key moment.
7. Evaluation script: detection rate, false alerts, factual accuracy across all scenario matches; results pushed to Foundry evaluations once the live models are configured.

**Done when:** replaying a scenario match locally produces audited insights and overlays in three languages and five audience modes, with the evaluation scorecard.

### Phase 4 — Studio app and deployment (Oct 20–23)

1. Cosmos DB ledger and Service Bus queue adapters (same interfaces as the Phase 3 in-memory ones; built 2026-10-09, `replay --ledger cosmos --queue servicebus`), resources created by hand first and then captured in the Bicep below; the engine reads the feed through the feed adapter.
2. Event-store adapter for the Azure Data Explorer free cluster (tables in the architecture doc; local-file fallback), written to by the engine and orchestrator.
   - Live service (`pitch-ledger live`, built 2026-10-09): feed -> engine (detector re-run on data so far, settled triggers numbered like a batch run) -> trigger queue -> agents -> ledger, writing the event store as it goes.
3. FastAPI `/api/v1`: matches, insights, overlays, approvals (built 2026-10-09: public fan endpoints, producer endpoints behind Entra ID with fail-closed sign-in, rate-limited decisions); viewer preferences stay in the Studio for now.
4. Next.js Studio app: producer console (Entra sign-in, approve/block queue with evidence drill-down) and fan view (match timeline, overlay layer, mode / club / player / metric / language selectors). Built 2026-10-09: fan view (mode, language, club; 5 s refresh) and producer console (queue, audit trail, metrics, evidence, approve/block) against the API in dev auth; ops-alert panel built 2026-10-10; Entra sign-in (MSAL) to follow.
5. Web PubSub live push (built 2026-10-09: change signals per match group, negotiate endpoints, Studio hook with polling fallback).
6. Bicep for all Azure resources; GitHub Actions build to GHCR and deploy to Container Apps.
7. ADX dashboard (control room) over the event store, tile queries kept in the repo; ops-alert records raised by the engine, orchestrator, queue and model manager, shown and acknowledged in the producer console (built 2026-10-10: feed gap and lag, validation at live ingest, blocked insights, dead letters, model fallbacks; `stream --fault-at` stages a fault for the demo).
8. Managed identities, key auth disabled, content-safety policies, Application Insights and Foundry tracing.

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
