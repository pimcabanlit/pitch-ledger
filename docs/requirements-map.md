# Requirements map: the challenge brief against Pitch Ledger

Every point of the hackathon brief (section 4.1, *Hackathon Challenge*) mapped to what Pitch Ledger does, with a status and where to see it. Checked against the code and the live deployment on 2026-10-11. Update a row whenever its status changes.

**Statuses**

| Status | Meaning |
| --- | --- |
| **Implemented** | Built, tested, and visible in the live demo or its records |
| **Partial** | Some of it is built; the row says what is missing |
| **Not implemented** | Not built; the row says why |
| **Nice to have** | Not asked for directly; a cheap improvement |
| **Beyond the brief** | Not asked for; strengthens the entry (mostly enterprise controls) |

**Summary**

| Area | Implemented | Partial | Not implemented |
| --- | --- | --- | --- |
| (i) Pipeline: ingest, interpret, explain, render | 4 | 0 | 0 |
| (i)(e) Personalization | 8 | 0 | 0 |
| (ii) Real-time match intelligence | 7 | 0 | 1 |
| (iii) Narrative generation | 4 | 0 | 0 |
| (iv) Explainability | 2 | 2 | 0 |
| (v) Data innovation | 1 | 0 | 0 |
| (vi) Multi-language storytelling | 1 | 0 | 0 |
| Fit: live, studio, broadcast, streaming, any market | 4 | 0 | 0 |
| **Total** | **31** | **2** | **1** |

What is left: chaos and tempo are on screen and explained, but not yet narrated in insight copy. Auto-eventing from video is out of scope with synthetic data. On 2026-10-11, milestones, pass quality, distance and ball speed went on screen as badges. Viewer choice (favourite clubs and player, follow-a-player, chosen metric, remembered preferences), names on the pitch, and chaos and tempo charts followed the same day, with no model calls; see [Gaps to close in Phase 5](#gaps-to-close-in-phase-5).

## (i) One pipeline with five stages

| # | Requirement | Status | What Pitch Ledger does | Where to see it |
| --- | --- | --- | --- | --- |
| a | **Ingest** synthetic match events as they happen: passes, shots, tackles, possession changes, pressure events, match metadata | **Implemented** | The Match Lab streams a match to Azure Event Hubs at match pace (`stream`, up to 25x): 16 event types (pass, ball receipt, carry, dribble, shot, pressure, tackle, interception, clearance, block, ball recovery, goalkeeper, foul, card, substitution, out of play) plus period markers, every row carrying its possession ID, and 1 Hz tracking for all 22 players. The match metadata (teams, players, venue, referee, kick-off) opens the stream and repeats every 10 s. The live service validates each row as it arrives; rejects raise ops alerts. Measured in Azure: every row arrives, feed lag p95 under 5 s at 25x | `src/pitch_ledger/feed/`, `live/`; Container Apps jobs `pitch-ledger-feed` and `pitch-ledger-live`; ADX dashboard tile *Feed health*; docs/azure-setup.md, *Measured (2026-10-10)* |
| b | **Interpret** them into meaningful statistics, patterns and context | **Implemented** | Deterministic engine: xG, pass length, pass difficulty, pass ball speed, possession, pass accuracy against expected, PPDA, tempo, turnovers, control (0–100 per team), chaos, player physical metrics, all over rolling 5-minute windows. A detector turns them into evidence-backed triggers: momentum shift (build-up or pressing), goal, penalty, sending-off and its impact, player form, milestones, speed alerts, pass quality, recaps. Team state is snapshotted every match minute to the ADX event store | `src/pitch_ledger/engine/`; `uv run pitch-ledger detect data/M0002`; ADX dashboard (control over time, latest window per team) |
| c | **Explain** why a moment matters, not only that it happened | **Implemented** | The Explainer writes what happened, then why it matters, from metrics and context signals chosen in code. Examples: what a goal does to the score, the opponent's response to a spell, a penalty's stakes, minutes left. The Auditor recomputes every number from the raw events and checks the meaning before anything airs | Studio → any insight → *Insight Story* (evidence, audit trail); Foundry → Tracing |
| d | **Render** the insight into something that can sit on screen beside the match | **Implemented** | Every aired insight becomes timed overlay objects: period, clock, display time, priority, template, text, and structured `data`. They show as captions synchronized to the Match Centre's replay clock, over a 2D pitch drawn from tracking. A broadcast package endpoint gives a rendering partner the graphics for one insight, and Web PubSub pushes a signal when something airs | Studio → Match Centre; `GET /api/v1/matches/{id}/overlays`, `GET /api/v1/insights/{id}/broadcast` |

### (i)(e) Personalize: the same intelligence for a data-hungry analyst and a casual fan

| Requirement | Status | What Pitch Ledger does | Where to see it |
| --- | --- | --- | --- |
| Analyst and casual fan as **two genuinely different experiences** | **Implemented** | Five audience modes per insight, each with its own depth rules, enforced by an editorial check. *Analyst*: precise, two or three numbers with the key comparison. *Casual*: plain words and at most one figure. Every aired insight has all five modes in all three languages (15 overlays) | Studio fan view → *Mode*; scorecard gate *every aired insight in 5 modes x 3 languages* |
| Customized modes | **Implemented** | Analyst, casual, club focus, player focus, single metric | Fan view *Mode* selector |
| Favourite club(s) | **Implemented** | Star any number of clubs. Their matches are listed first and marked, and the Match Centre marks them in its club filter (both clubs, or one). *Club focus* copy is written for a club's supporters. Remembered in the browser | Match Centre → *Your view* (stars, *Show*); match list |
| Favourite player(s) | **Implemented** | Pick a player from either squad in *Your view*. Player IDs are stable across matches, so the favourite carries over to the club's other matches. Remembered in the browser | Match Centre → *Your view* → *Follow a player* |
| Player-focused mode that follows a player **throughout the match** | **Implemented** | The followed player has a ring on their dot all match. A card shows their match totals as of the replay clock (passes and accuracy, shots, goals, xG, distance, top speed), whether they are on the pitch, and their aired moments (a click jumps there). Their insights are marked in the feed. Totals come from `GET /api/v1/matches/{id}/players/{player_id}/timeline`, computed from events and tracking with the engine's definitions. *Player focus* copy still names each moment's own player | Match Centre → player card |
| Language preference | **Implemented** | English, Spanish, Filipino for every aired insight; numbers kept exactly (Spanish decimal commas) and checked | Fan view *Language* selector |
| A single performance metric the viewer cares about | **Implemented** | The viewer picks *Your metric*: control, possession, pass accuracy, tempo, PPDA, chaos, xG or speed. The chart draws it up to the replay clock with its current value per team, and every insight that carries that metric shows the figure as a chip in the feed. The *single metric* mode's copy still shows the one figure code chose for each moment | Match Centre → *Your view* → *Your metric*; chart |
| Timed, machine-readable output for synchronized overlays on a live feed (the rendering partner's production path) | **Implemented** | See (d): overlay objects with clock, display time, priority and structured data; the broadcast package endpoint | API (see d) |

## (ii) Real-time match intelligence

| Requirement | Status | What Pitch Ledger does | Where to see it |
| --- | --- | --- | --- |
| Aggregate events into meaningful football **states and statistics** | **Implemented** | Rolling team state per minute (control, possession, pass accuracy, turnovers, PPDA, tempo), live in the event store as the match plays | ADX dashboard; Match Centre control chart |
| **Player identification**: real-time player name tagging | **Implemented** | Players are named in captions, overlays and the match feed as moments happen. While a caption is about a player, their name appears beside their dot on the pitch. Otherwise the pitch shows shirt numbers only, so it stays readable | Match Centre pitch |
| Speed indicators triggered at predefined thresholds | **Implemented** | A badge when a player first reaches 34 km/h, and for shots over 100 km/h. Auto-approved, no model calls, capped at one per team per half plus new match records | Fan view and Match Centre speed badges |
| Distance indicators triggered at predefined thresholds | **Implemented** | A milestone badge when a player becomes the first of their team past 10 km (from tracking); 5 km milestones are kept in the ledger. Auto-approved, no model calls | Match Centre captions (*milestone badge*); `distance_milestone` triggers |
| **Pass quality**: distance, accuracy, difficulty rating | **Implemented** | A pass badge for the hardest completed pass so far in the match (difficulty 65+ and 15+ m forward to qualify): difficulty rating (0–100), length, forward distance and ball speed. Pass accuracy against expected for difficulty appears in momentum insights, and a pass-milestone badge gives the first player of each team to 50 completed passes, with their accuracy. The Match Centre also has a pass network | Match Centre captions (*pass badge*, *milestone badge*); `src/pitch_ledger/engine/metrics.py` |
| **Speed metrics**: shot speed | **Implemented** | Shot speed in badges and in goal context ("with a 100.6 km/h shot") | Speed badges; goal insights |
| **Speed metrics**: ball speed | **Implemented** | Pass ball speed (from distance and travel time) on the pass badge's analyst line; shot speed as above | Pass badge, analyst mode |
| **Auto-eventing**: a model over an incoming video feed generates pass and speed metrics | **Not implemented** | There is no video in a synthetic-data challenge. The Match Lab generates events and tracking already labelled, the stage auto-eventing would produce. The feed adapter is where a computer-vision provider's events would plug in (same row schema) | README, production path (Phase 6) |

## (iii) Narrative generation

| Requirement | Status | What Pitch Ledger does | Where to see it |
| --- | --- | --- | --- |
| Key narratives | **Implemented** | Momentum shifts (build-up collapse, pressing change), goals with what they do to the match, penalties with their stakes, sending-offs and their impact 10 minutes later | Fan view; Producer console |
| Player-performance context | **Implemented** | Player form (shots and xG in a 15-minute spell, share of the team's shots); the scorer's tally in goal copy | *player_form* insights; goal insights |
| Notable milestones | **Implemented** | Brace and hat-trick lead the goal copy ("completes a hat-trick"); milestone badges for the first player of each team to 50 completed passes and past 10 km. Later milestones (75th, 100th pass; 5 km) are kept in the ledger | Goal insights; milestone badges |
| Match recaps | **Implemented** | Half-time and full-time recaps by the Narrator: whether the chances support the scoreline, and how the match moved | *recap* insights |

## (iv) Explainability

| Requirement | Status | What Pitch Ledger does | Where to see it |
| --- | --- | --- | --- |
| Explain why moments matter | **Implemented** | Every insight has a "why it matters" explanation grounded in code-computed metrics, with cited evidence events and a full audit trail. Foundry's groundedness judge scores the model-written copy 5.0 out of 5 | Insight Story page; Foundry → Evaluations |
| Control vs chaos | **Partial** | A control score per team drives momentum insights. The Match Centre charts control per team and the match chaos score (0–100: possession changes per minute, share of play under pressure, duels per minute) minute by minute, with glossary tips explaining both. Missing: chaos as a signal in insight copy | Match Centre chart (*Control*, *Chaos*); `GET /api/v1/matches/{id}/analytics` |
| Tactical pressure changes | **Implemented** | The pressing variant of the momentum shift explains a change in pressure with PPDA and turnovers forced; the *high press switch* scenario (M0003) shows it | M0003 insights |
| Game rhythm patterns | **Partial** | Tempo (passes per minute of possession) is computed per team and minute. It is shown in the ADX dashboard and on the Match Centre chart, with PPDA for pressing intensity. Missing: tempo in insight copy | Match Centre chart (*Tempo*, *PPDA*); ADX tile *Latest window per team* |

## (v) Data innovation

| Requirement | Status | What Pitch Ledger does | Where to see it |
| --- | --- | --- | --- |
| Create or extend synthetic, football-realistic datasets: passes, shots, tackles, possession changes, pressure events, match metadata | **Implemented** | The Match Lab generator: seeded, reproducible matches with fictional clubs and players. It writes the 16 event types with possession sequences and 1 Hz tracking for 22 players, calibrated only to published aggregate benchmarks (realism bands), and validated by row, sequence and match rules. Planted scenarios (late collapse, high press switch, red card, hot streak) are defined in YAML. Six demo matches, as JSONL (live-feed format) and Parquet | `src/pitch_ledger/matchlab/`, `scenarios/`; `uv run pitch-ledger generate-all`; `validate`, `realism`; docs/event-schema.md |

## (vi) Multi-language storytelling

| Requirement | Status | What Pitch Ledger does | Where to see it |
| --- | --- | --- | --- |
| Clear natural-language output in multiple languages for global studio and streaming | **Implemented** | Every aired insight in English, Spanish and Filipino, for all five modes. The Translator keeps every number exactly, and a code check rejects any translation whose numbers do not match; a rejected line falls back to a template | Fan view *Language* selector |
| More languages | **Nice to have** | Languages are a setting plus one phrase table per language; each extra language adds translator calls per aired insight | (gap 7) |

## Fit with the intended use

| Requirement | Status | What Pitch Ledger does | Where to see it |
| --- | --- | --- | --- |
| Responsive enough for live play | **Implemented** | Live service in Azure: feed lag p95 under 5 s at 25x match pace; insights flow while the match streams | docs/azure-setup.md, *Measured* |
| Clear enough for real-time narratives | **Implemented** | House-style and length limits checked in code (headline ≤ 10 words, overlay ≤ 140 characters, casual ≤ 1 figure, no clichés or unsupported claims) | Scorecard gate *aired copy passes the editorial check* |
| Suitable for studio, broadcast and streaming | **Implemented** | Studio producer console (approve or block with evidence), fan view for streaming, broadcast package endpoint for a rendering partner | Studio; API |
| Flexible enough for fans in any market | **Implemented** | Modes × languages × club filter per insight. Partial points (favourite player, chosen metric) are in the (i)(e) rows above | Fan view |

## Beyond the brief

Not asked for, but they make the output trustworthy and operable, the case for *Best Enterprise Solution*.

| Capability | Status | Where to see it |
| --- | --- | --- |
| Insight ledger: every insight, audit step, approval and overlay as a record (Cosmos DB) | **Beyond the brief** | Insight Story page; Cosmos DB |
| Auditor: recomputes every number from raw events through read-only MCP tools before air; a planted error is caught every time | **Beyond the brief** | Scorecard gate *every planted error caught*; Foundry → Tracing (`audit check (code)`) |
| No numbers from a model: every statistic computed in Python | **Beyond the brief** | Scorecard gate *every aired insight recomputes exactly*; Foundry code-check graders |
| Producer approval with Microsoft Entra ID sign-in | **Beyond the brief** | Studio → Producer |
| Ops alerts: feed gaps and lag, validation rejects, blocked insights, content-filter and template fallbacks, dead letters | **Beyond the brief** | Producer console ops panel; ADX tile *Ops alerts* |
| Control room: ADX dashboard over the event store | **Beyond the brief** | ADX dashboard `Pitch Ledger control room` |
| Foundry tracing of every agent call, linked per insight | **Beyond the brief** | Foundry → Tracing |
| Foundry evaluations: code checks plus groundedness, coherence and fluency judges per aired insight | **Beyond the brief** | Foundry → Evaluations |
| Content safety: the content filter blocks a prompt-injection probe; blocked calls fall back to templates with an alert | **Beyond the brief** | `check-models --safety` |
| Key-less access: Entra ID only on Foundry, Cosmos DB, Event Hubs, Service Bus, Web PubSub and Application Insights | **Beyond the brief** | docs/azure-setup.md, *Enterprise controls* |
| Infrastructure as code (Bicep), CI and deploy (GitHub Actions), scale to zero | **Beyond the brief** | `infra/`, `.github/workflows/` |
| Cost controls: token caps, call budgets, small model for rewrites, speed-badge cap, log caps | **Beyond the brief** | docs/architecture.md, *Cost plan* |
| Season history: one record per finished match (result, team and player stats, goals) in Cosmos DB and ADX, and scorecard runs in ADX. Audited season facts in insights: the scorer's league goals, the first hat-trick of the season, form over the last five matches, the last meeting | **Beyond the brief** | `pitch-ledger history show`; ADX tables `MatchResults`, `TeamMatchStats`, `PlayerMatchStats`, `EvalRuns`; M0002's hat-trick insight |

## Gaps to close in Phase 5

In priority order: what each closes, and whether it needs model calls.

| # | Gap | Closes | Model calls |
| --- | --- | --- | --- |
| 1 | **Done 2026-10-11.** **Favourite player and follow-a-player view**: pick a player (from either squad); their insights first, their stats as of the replay clock, their dot highlighted all match, *player focus* copy for them where it applies | Favourite player(s); player-focused mode throughout the match | None (stats from the engine) |
| 2 | **Done 2026-10-11.** **Choose the single metric**: the viewer picks the metric (shot speed, xG, possession, pass accuracy, control…); the fan view shows that metric's live value and the insights that carry it | A single performance metric the viewer cares about | None |
| 3 | **Done 2026-10-11.** Milestones and pass quality air as badges on firsts and new bests (template copy, code audit, auto-approved); brace and hat-trick fold into the goal | Milestones; distance indicators; pass quality; ball speed | None |
| 4 | **Chart done 2026-10-11** (chaos, tempo and PPDA on the Match Centre chart, with glossary tips). **Open:** chaos and tempo as context signals in momentum copy ("the match got more open") | Control vs chaos; game rhythm patterns | None for the chart; the signals only change what the Explainer is given |
| 5 | **Done 2026-10-11.** **Names on the pitch**: the name shows beside the dot while a caption is about that player (owner's choice: a pitch full of names is hard to read) | Player name tagging | None |
| 6 | **Done 2026-10-11.** **Remember preferences**: favourite clubs (more than one), player, mode, metric and language, kept in the browser | Favourite club(s), fuller | None |
| 7 | **More languages** (for example French, Portuguese, Arabic) | Fans in any market, fuller | Translator calls per extra language |
| — | Auto-eventing from video | Out of scope with synthetic data; described as the production path | — |
