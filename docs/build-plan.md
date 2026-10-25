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
| Agent flow by insight type | Momentum shift, key moment, player form and recap: Explainer (Narrator for recaps) ⇄ Auditor, producer approval, five audience modes in three languages. Speed alerts: template copy, code audit, auto-approved; capped at one per team per half plus any new match record of its kind (2026-10-11). Milestones and pass quality: badges like speed alerts, on firsts and new bests only (2026-10-11: the first player per team to 50 completed passes or past 10 km, the hardest pass so far; brace and hat-trick fold into the goal). Insight depth (2026-10-10): context metrics per rule, repeat speed alerts folded, copy that explains significance by mode, an editorial check, and 8 model calls per insight instead of 17 |
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
6. `matchlab/tracking.py`: 1 Hz player positions and speeds consistent with events. Refined 2026-10-10: timed runs put each actor on the ball at their event, and dead balls use set-piece shapes (`matchlab/set_pieces.py`).
7. CLI: `generate`, `validate`, `realism`. Outputs JSONL (live feed format) and Parquet/CSV.
8. Tests: schema round-trips, every validation rule has a passing and failing case, the baseline match passes validation and lands inside all realism bands, generation is reproducible from the seed.

**Done when:** `pitch-ledger generate --scenario none` produces a valid, realistic match twice with identical output for the same seed.

### Phase 2 — Engine, scenarios and detector (Oct 12–14)

1. `engine/`: derived metrics, rolling 5-minute windows, PPDA, tempo, control and chaos scores, player physical metrics, milestones and speed alerts.
2. Planted scenarios as YAML; generator applies their effects.
3. `engine/detector.py`: triggers with evidence event IDs.
4. Scenario tests: each scenario is detected within its time limit; the baseline produces no false momentum alerts.
5. **Azure setup in parallel:** personal subscription, budget alerts, resource group, Foundry project with two model deployments (confirm region availability).
6. ~~**Latency spike:** Fabric trial, Eventstream → local Python consumer; time the hop.~~ Resolved 2026-10-09: the Fabric trial is not available, so the live feed is Azure Event Hubs. Measured from a cloud session through its proxy: no loss, p50 2.5–3.3 s, p95 4.6–7.3 s (pessimistic; re-measure from Container Apps). Re-measured 2026-10-10 from Container Apps: one row per message capped the live job at ~350 rows a second, below a 25x stream; tracking rows are now packed, and a full Foundry live run of M0002 keeps p95 lag under 5 s (docs/azure-setup.md, Measured 2026-10-10).

**Done when:** all 5 scenarios pass detection tests locally.

### Phase 3 — Agents and ledger (Oct 15–19)

1. `agents/model_manager.py` (single entry point to Foundry; token caps; retries; fallback).
2. Stats MCP server with read-only tools: `get_events`, `get_team_state`, `get_player_stats`, `recompute_metric`.
3. Explainer, Auditor (maker-checker, max 2 rounds), Narrator (handoff at breaks), Personalizer and Translator (concurrent), Overlay builder (code).
4. Ledger repository interface: in-memory for tests, local JSONL files for replays (Cosmos DB adapter in Phase 4).
5. Trigger queue interface with retry and dead letter: in-memory adapter (Service Bus adapter in Phase 4).
6. **Planted error test:** inject a wrong number into an Explainer draft and assert the Auditor blocks or corrects it. This is the demo's key moment.
7. Evaluation script: detection rate, false alerts, factual accuracy across all scenario matches; results pushed to Foundry evaluations once the live models are configured. Built 2026-10-11: `pitch-ledger foundry-eval` publishes every aired insight in the ledger to Foundry evaluations (code checks + groundedness, coherence, fluency; scorecard gates as metadata). First runs: 55/55 aired insights pass every code check; M0002's Foundry-written copy scores groundedness 5.0, coherence 4.0, fluency 3.5 (all pass).

**Done when:** replaying a scenario match locally produces audited insights and overlays in three languages and five audience modes, with the evaluation scorecard.

### Phase 4 — Studio app and deployment (Oct 20–23)

1. Cosmos DB ledger and Service Bus queue adapters (same interfaces as the Phase 3 in-memory ones; built 2026-10-09, `replay --ledger cosmos --queue servicebus`), resources created by hand first and then captured in the Bicep below; the engine reads the feed through the feed adapter.
2. Event-store adapter for the Azure Data Explorer free cluster (tables in the architecture doc; local-file fallback), written to by the engine and orchestrator.
   - Live service (`pitch-ledger live`, built 2026-10-09): feed -> engine (detector re-run on data so far, settled triggers numbered like a batch run) -> trigger queue -> agents -> ledger, writing the event store as it goes.
3. FastAPI `/api/v1`: matches, insights, overlays, approvals (built 2026-10-09: public fan endpoints, producer endpoints behind Entra ID with fail-closed sign-in, rate-limited decisions); viewer preferences stay in the Studio for now.
4. Next.js Studio app: producer console (Entra sign-in, approve/block queue with evidence drill-down) and fan view (match timeline, overlay layer, mode / club / player / metric / language selectors). Built 2026-10-09: fan view (mode, language, club; 5 s refresh) and producer console (queue, audit trail, metrics, evidence, approve/block) against the API in dev auth; ops-alert panel built 2026-10-10; Entra sign-in (MSAL) built 2026-10-10: any Microsoft account for the public demo, the Producer role for production.
4b. Match Centre (2026-10-10): replay clock, score as of the clock, 2D pitch from tracking, broadcast captions, feed, deep links (step A); control chart, shot map and pass network synced to the clock (step B). Player stats as of the clock followed in Phase 5 (the followed-player card).
5. Web PubSub live push (built 2026-10-09: change signals per match group, negotiate endpoints, Studio hook with polling fallback).
6. Bicep for all Azure resources; GitHub Actions build to GHCR and deploy to Container Apps. Written 2026-10-10: `Dockerfile`, `studio/Dockerfile`, `infra/main.bicep` (+ modules), `.github/workflows/deploy.yml`; deployed to Container Apps since 2026-10-10 (docs/azure-setup.md, Deployment).
7. ADX dashboard (control room) over the event store, tile queries kept in the repo; ops-alert records raised by the engine, orchestrator, queue and model manager, shown and acknowledged in the producer console (built 2026-10-10: feed gap and lag, validation at live ingest, blocked insights, dead letters, model fallbacks; `stream --fault-at` stages a fault for the demo).
8. Managed identities, key auth disabled, content-safety policies, Application Insights and Foundry tracing. Built 2026-10-11: OpenTelemetry tracing (one trace per trigger and producer decision, agent spans nested) to Application Insights with Entra-only ingestion, read by Foundry's Tracing page; content-filter handling and `check-models --safety`; `infra/enterprise-controls.sh` switches key auth off on every service (docs/azure-setup.md, Enterprise controls).

**Done when:** the public link replays a match end to end in Azure.

### Phase 5 — Product readiness (Oct 12–23)

1. **Homepage.** A landing page at the Studio root that says what Pitch Ledger is in one line, shows the pipeline (ingest → interpret → explain → render → personalize), and routes each visitor: fan view, producer console (sign-in), Match Centre, and "how it works" (ledger, Auditor, evidence). It shows live status (latest matches, what is airing) and links to the judges' instructions. No trademarks; fictional clubs only.

   Done 2026-10-11. The homepage has:
   - a hero with entry points to the demo match (M0002) and the producer console;
   - the five pipeline steps;
   - "how trust works" (code-computed numbers, evidence, Auditor, producer);
   - every match with its score and aired-insight count;
   - the fan and producer views.

   The match list moved to `/matches`. The judges' instructions link comes with Phase 6.
2. **UI and UX improvements.** Make the Studio intuitive for a first-time visitor:
   - consistent navigation across pages;
   - clear empty, loading and error states;
   - plain-language labels and tooltips for football and pipeline terms (xG, PPDA, control, audit statuses);
   - the selectors the challenge names: favourite club, favourite player, player-focus mode, a single performance metric, language;
   - mobile layout and accessibility (contrast, keyboard, screen reader).

   Check it with a short walkthrough: a new user finds a match, sees an insight and its evidence, and switches mode and language without help.

   Done 2026-10-11:
   - one navigation bar with the current page marked, a skip link and a footer;
   - skeletons and error-with-retry states;
   - glossary tips for control, chaos, tempo, PPDA, possession, pass accuracy, xG, speed, modes and statuses;
   - a Match Centre *Your view* panel (mode, language, club filter, favourite clubs, follow a player, your metric), remembered in the browser;
   - a followed-player card with totals as of the replay clock;
   - a metric chart (control, possession, pass accuracy, tempo, PPDA, chaos, xG, speed);
   - the captioned player's name on the pitch;
   - an `aria-live` caption for screen readers.

   Checked at 1280 px and 390 px. This closed requirements-map gaps 1, 2, 5 and 6 and the chart half of gap 4.

   Done 2026-10-11: **On-pitch scoreboard (broadcast score bug).** Move the Match Centre scoreboard from its own card onto the pitch, as a slim bar centred at the bottom of the picture, like a TV score bug:
   - home colour dot and name, the score as of the replay clock, away name and colour dot;
   - the match minute and the half (1st half, 2nd half, Full-time);
   - no venue.

   The insight caption (lower third) moves up so the two never overlap. On phones the bar stays readable: short club names (NBR, CRW), with the caption below the pitch as today. The separate scoreboard card is removed.

   Dropped 2026-10-11 (owner): **Sticky "Your view" panel.** Built, then reverted: pinned panels covered too much of the page. Instead the panel lost its header row (title, "remembered on this device", favourite stars) to save space; the favourite-club stars moved onto the match cards.

   Done 2026-10-11: **Homepage hero and a producer page.** The homepage opens with a *Pitch Ledger Studio* hero (title, tagline, one supporting line, *Watch the demo match* and *I'm a producer*) beside a line-art football over pitch markings (an SVG generated by `studio/scripts/hero-art.py`). *I'm a producer* opens `/for-producers`: "Match intelligence you can audit before it airs", *Try it now* (the producer console) and *How trust works* (moved from the homepage).
3. **Quality: requirement-by-requirement map.** A `docs/requirements-map.md` mapping every point of the challenge (section 4.1: the five pipeline stages (a)–(e) including the personalization examples, real-time intelligence (player identification, pass quality, speed metrics, auto-eventing), narrative generation, explainability, data innovation, multi-language) to what Pitch Ledger does. Each row gets a status:
   - **Implemented**, with where it lives and how to see it;
   - **Partial**, with what is missing;
   - **Not implemented**, with why (e.g. auto-eventing from video, since the data is synthetic);
   - **Nice to have** or **Beyond the brief** (enterprise controls, Auditor, evaluations).

   Gaps worth closing are fed back into this phase before Phase 6. Done 2026-10-11: [requirements-map.md](requirements-map.md): 22 implemented, 10 partial, 2 not implemented, with seven gaps to close in priority order (favourite player and follow-a-player, choosing the single metric, airing milestones and pass quality, chaos and tempo on screen, names on the pitch, saved preferences, more languages).
4. **Historical data.** Keep each match's results so future games can use them: per-match summaries, team and player match stats, insights and their audit outcomes, scorecard and evaluation results. Store them durably, append-only, keyed by match, season and club. That means extending the ADX event store and the Cosmos ledger rather than a new service.

   The engine and agents can then use history in context (e.g. "first hat-trick this season", form over the last five matches). Every historical figure stays computed in code and cited, never recalled by a model. The current "no data on seasons or records" rule in the prompts changes only once that data exists. Lakehouse history stays a stretch goal.

   Done 2026-10-11. Storage:
   - one record per finished match (result, team and player match stats, goals), built from the match rows by the engine;
   - kept in the Cosmos DB ledger container, in a partition per season (`history:2026-27`), and in the ADX tables `MatchResults`, `TeamMatchStats` and `PlayerMatchStats`;
   - scorecard runs in `EvalRuns` (`evaluate --store`); insight outcomes stay in `InsightStatus`;
   - written by replays, by the live service at full time, and by `history build`;
   - the demo matches are one matchday a week from 2026-08-22.

   Season facts in goals and full-time recaps:
   - the scorer's league goals and the first hat-trick of the season;
   - form over the last five league matches;
   - the last meeting this season.

   They come only from matches that kicked off earlier. The trigger cites those matches, and the Auditor recomputes the facts from the same records. "Season" is allowed in copy only when an insight carries season facts.
5. **Cost monitoring.** Done 2026-10-11 in code and docs:
   - per-service cost notes and observed model costs (docs/azure-setup.md);
   - per-run model call and token counts, token caps, call budgets and log caps;
   - each run's model cost and cost per aired insight, priced from tokens in one config place (`ModelSettings`);
   - printed by `replay` and `live`, kept in `run.json`, the scorecard and the ADX table `RunUsage` (replay and live jobs, producer decisions, evaluations);
   - ADX dashboard tiles for cost per match, per insight and per day;
   - a Cost Management budget with alerts on the subscription;
   - judging guardrails (docs/azure-setup.md, *Cost monitoring*).

6. **Fan story and producer process flow.** Done 2026-10-17 (PRs #7 and #8):
   - "Tell me more" is a three-beat story for fans (the buildup, what just happened, what it could mean) in the reader's audience mode and language: plain words for fans, figures for analysts;
   - the Narrator and Translator write the beats from facts computed in code; each beat is number- and style-checked, else its template airs; stories are stored in the ledger;
   - the pipeline view (stages, metrics, evidence, agent handoffs, published versions, the story) moved to the producer console as a vertical, clickable flow; ops alerts start collapsed;
   - live on M0002 after a Foundry re-run; a full insight now costs 10 model calls, and the per-run budget is 160.

**Done when:** the homepage and UI changes are live, the requirements map is complete with every gap either closed or labelled, match results are stored as history and readable by the engine, and cost per match plus a confirmed budget alert are documented.

### Phase 6 — Polish, video, submission (Oct 24–27)

1. README: problem, architecture diagram, how to run locally, how to test the live demo, enterprise controls, production path, cost, evaluation scorecard, screenshots.
2. Record the demo video (under 2 minutes, no trademarks, no copyrighted music).
3. Update the project page description to match what is actually built.
4. Testing instructions for judges (public link; credentials only if needed).
5. Oct 26 buffer; submit early on Oct 27.
6. Done in October: the Azure subscription moved to pay-as-you-go, so the demo stays live until Nov 20.

## Demo script (target 1:50)

1. 0:00 Problem in one line; Pitch Ledger in one line.
2. 0:10 Synthetic match running live; control-room dashboard.
3. 0:25 Planted collapse at 60': momentum shift detected; Explainer's draft appears with evidence.
4. 0:45 Auditor catches an over-reaching number and blocks it; corrected insight passes.
5. 1:00 Producer approves; overlay fires on screen.
6. 1:10 Same moment for an analyst (numbers) and a casual fan (one sentence), in English and Filipino or Spanish; "Tell me more" tells it as a three-beat story.
7. 1:30 Full-time recap with evidence trail; trace and evaluation scorecard.
8. 1:45 Architecture slide and close.

## Stretch goals (not built)

- Lakehouse medallion history.
- Fabric data agent "ask the match" connected to a Foundry agent.
- Orchestrator as a Foundry hosted agent.
- SPADL export.
