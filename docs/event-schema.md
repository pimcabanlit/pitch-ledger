# Match Event Schema v1

Data contract for the Pitch Ledger pipeline. Every stage reads from one event table: one row per on-pitch event, with the same columns every time. All data is synthetic; all clubs, players, venues and officials are fictional.

## Design principles

| Layer | Borrowed from | What it gives us |
| --- | --- | --- |
| Core columns | [SPADL](https://socceraction.readthedocs.io/en/latest/documentation/spadl/spadl.html) (uniform attributes per action) | One flat table, easy in SQL and pandas, exportable to standard tools |
| Context columns | [StatsBomb open data](https://github.com/hudl/open-data) event spec (structure only) | Possession chains, pressure, play patterns, linked events: the raw material for explainability |
| Tracking table | [Metrica sample data](https://github.com/metrica-sports/sample-data) (events synced with positions) | Player speed, distance and sprint alerts |

- **Raw facts only in the event table.** Anything computable (pass length, ball speed, xG, indices) is derived in code, so every number on screen traces back to rows.
- **Every row has a stable ID.** Insights cite event IDs as evidence; the Auditor checks them.
- **Same schema for live and batch.** The live feed sends rows as JSON lines; analytics reads the same fields as a table. On the feed (Azure Event Hubs, or a Fabric Eventstream custom endpoint), each message body is one row unchanged; the message properties carry `kind` (`event` or `tracking`) and `sent_at` (Unix seconds, for latency), and the partition key is `match_id` so a match's rows stay in order. Each match starts with one `kind = match` message whose body is the match's reference rows (the content of `match.json`: match, teams, players), so a consumer needs no shared files.
- **Reproducible generation.** Each match records its generator version and random seed.
- **Structure only, no copied data.** These formats inspire the layout; no real or provider match data is used or calibrated against.

## Conventions

| Convention | Rule |
| --- | --- |
| Pitch | Meters. x from 0 to 105, y from 0 to 68; origin at bottom-left |
| Direction of play | Stored so the **acting team always attacks toward x = 105**. When possession flips, coordinates flip: x' = 105 − x, y' = 68 − y |
| Goal | Centre of attacked goal at (105, 34); posts at y = 30.34 and 37.66 |
| Match clock | `clock_ms`: integer milliseconds since the start of the period; stoppage time continues past 45:00 |
| Broadcast time | `feed_ts_utc`: simulated wall-clock time the event is published (for overlay sync) |
| Periods | 1 = first half, 2 = second half |
| Match ID | `M` + 4 digits, e.g. `M0001` |
| Event ID | match ID + `-E` + 6-digit sequence, e.g. `M0001-E000412` |
| Team / player IDs | `T01`, `T02`; player `T01-P08` (team + shirt number) |
| Enumerations | lowercase snake_case, e.g. `ball_receipt` |

## Reference tables

**match** (one row per match)

| Field | Type | Example | Notes |
| --- | --- | --- | --- |
| match_id | string | M0001 | Primary key |
| competition | string | Harbour Coast League | Fictional |
| season | string | 2026-27 | |
| kickoff_utc | timestamp | 2026-10-24T14:00:00Z | |
| venue | string | Harbourside Park | Fictional |
| pitch_length_m, pitch_width_m | number | 105, 68 | Fixed in v1 |
| home_team_id, away_team_id | string | T01, T02 | |
| referee | string | A. Navarro | Fictional |
| scenario_id | string | late_collapse | Planted scenario, or `none` |
| generator_version | string | 0.1.0 | |
| random_seed | integer | 4211 | Regenerates the match exactly |

**team** (one row per team)

| Field | Type | Example | Notes |
| --- | --- | --- | --- |
| team_id | string | T01 | Primary key |
| name, short_name | string | Northbridge Athletic, NBR | Fictional |
| primary_color | string | #1B4F9C | Used by overlays |
| formation | string | 4-3-3 | |
| style_profile | enum | high_press | `possession`, `high_press`, `counter`, `low_block`, `direct` |

**player** (one row per squad player)

| Field | Type | Example | Notes |
| --- | --- | --- | --- |
| player_id | string | T01-P08 | Primary key |
| team_id | string | T01 | |
| name, display_name | string | Rafael Santos, R. Santos | Fictional |
| shirt_number | integer | 8 | |
| position | enum | cm | `gk`, `cb`, `fb`, `dm`, `cm`, `am`, `wg`, `st` |
| preferred_foot | enum | right | `left`, `right`, `both` |
| is_starter | boolean | true | |
| top_speed_kmh | number | 33.1 | Generator ceiling for this player |
| ratings | object | {pace: 78, passing: 84, finishing: 61, pressing: 80} | 1 to 99; drive generator behaviour only, never shown as facts |

## Event table: core fields (every row)

| Field | Type | Nullable | Example | Meaning |
| --- | --- | --- | --- | --- |
| event_id | string | no | M0001-E000412 | Unique; cited as evidence |
| match_id | string | no | M0001 | |
| seq_no | integer | no | 412 | Order within the match, no gaps |
| period | integer | no | 2 | 1 or 2 |
| clock_ms | integer | no | 783400 | Time since period start |
| feed_ts_utc | timestamp | no | 2026-10-24T15:16:03.400Z | Publish time on the live feed |
| team_id | string | yes | T01 | Team performing the event |
| player_id | string | yes | T01-P08 | Player performing the event |
| event_type | enum | no | pass | See event types |
| outcome | enum | yes | complete | Allowed values depend on type |
| start_x, start_y | number | yes | 40.2, 30.1 | Where the event started (m) |
| end_x, end_y | number | yes | 71.8, 45.0 | Where the ball or player ended (m). For a pass: the intended target (where it was received, or aimed if not completed); the next event shows where an incomplete pass was cut out or left the pitch |
| body_part | enum | yes | right_foot | `right_foot`, `left_foot`, `head`, `other` |
| duration_ms | integer | yes | 1200 | Event length; for passes and shots, ball travel time |

Nullable columns are empty only for events with no actor or no movement (period start, ball out of play).

## Event table: context fields

| Field | Type | Example | Meaning |
| --- | --- | --- | --- |
| possession_id | integer | 57 | Increments every time control changes team |
| possession_team_id | string | T01 | Team in control during this possession |
| play_pattern | enum | from_counter | `regular_play`, `from_kickoff`, `from_goal_kick`, `from_throw_in`, `from_corner`, `from_free_kick`, `from_counter` |
| under_pressure | boolean | true | An opponent within 5 m of the actor |
| nearest_opponent_m | number | 2.4 | Distance to closest opponent at the event |
| receiver_player_id | string | T01-P10 | Intended receiver (passes only) |
| related_event_ids | list of string | [M0001-E000411] | Directly linked events (pass → receipt, pressure → tackle) |
| score_home, score_away | integer | 1, 0 | Score **before** this event |
| players_on_pitch_home, players_on_pitch_away | integer | 11, 10 | After red cards |
| details | object (JSON) | {"pass_type": "through_ball"} | Type-specific fields (below) |

## Event types and outcomes

v1 uses 18 types. A goal is a `shot` with outcome `goal`.

| event_type | Meaning | Allowed outcomes | Has actor |
| --- | --- | --- | --- |
| pass | Ball kicked to a teammate | `complete`, `incomplete`, `out_of_play`, `offside` | yes |
| ball_receipt | Player receives a pass | `success`, `miscontrol` | yes |
| carry | Player runs with the ball | `success`, `dispossessed` | yes |
| dribble | Attempt to beat an opponent | `success`, `fail` | yes |
| shot | Attempt to score | `goal`, `saved`, `off_target`, `blocked`, `post` | yes |
| pressure | Defender closes down the ball carrier | `forced_turnover`, `no_effect` | yes |
| tackle | Challenge to win the ball | `won`, `lost` | yes |
| interception | Defender cuts out a pass | `won`, `lost` | yes |
| clearance | Ball kicked away from danger | `success`, `fail` | yes |
| block | Shot or pass blocked | `success` | yes |
| ball_recovery | Team regains a loose ball | `success`, `fail` | yes |
| goalkeeper | Save, catch, punch or claim | `save`, `catch`, `punch`, `claim`, `fail` | yes |
| foul | Foul committed | `free_kick`, `penalty` | yes |
| card | Yellow or red card | `yellow`, `second_yellow`, `red` | yes |
| substitution | Player replaced | `success` | yes |
| out_of_play | Ball leaves the pitch | `throw_in`, `corner`, `goal_kick` | no |
| period_start | Kick-off of a half | none | no |
| period_end | End of a half | none | no |

## Type-specific detail fields (`details` JSON)

Shot speed is generated directly, because a shot's flight time is too short to derive speed reliably from coordinates.

| event_type | Field | Type | Example | Notes |
| --- | --- | --- | --- | --- |
| pass | pass_type | enum | through_ball | `short`, `long`, `cross`, `through_ball`, `switch`, `back_pass` |
| pass | height | enum | ground | `ground`, `low`, `high` |
| pass | set_piece | enum | none | `none`, `corner`, `free_kick`, `throw_in`, `goal_kick`, `kick_off` |
| shot | shot_type | enum | open_play | `open_play`, `penalty`, `free_kick`, `header` |
| shot | shot_speed_kmh | number | 104.6 | Generated peak ball speed |
| shot | on_target | boolean | true | |
| shot | goalkeeper_id | string | T02-P01 | |
| shot | assist_event_id | string | M0001-E000409 | Setup pass, if any |
| shot | assist_type | enum | through_ball | Copied from assisting pass; xG input |
| pressure | target_player_id | string | T02-P04 | Player being pressed |
| pressure | counterpress | boolean | true | Within 5 s of losing the ball |
| tackle, interception | opponent_player_id | string | T02-P07 | Player who lost the ball |
| foul | fouled_player_id | string | T02-P09 | |
| card | reason | enum | foul | `foul`, `dissent`, `time_wasting`, `second_yellow` |
| substitution | player_in_id, player_out_id | string | T01-P17, T01-P08 | |
| goalkeeper | action | enum | dive_save | `dive_save`, `standing_save`, `catch`, `punch`, `claim` |

## Tracking table: `player_tracking` (one row per player per second)

| Field | Type | Example | Meaning |
| --- | --- | --- | --- |
| match_id | string | M0001 | |
| period | integer | 2 | |
| clock_ms | integer | 783000 | Every 1,000 ms (1 Hz, decided) |
| player_id | string | T01-P08 | |
| x, y | number | 42.7, 31.0 | Position (m), oriented so the **player's own team** attacks toward x = 105 (same rule as events) |
| speed_kmh | number | 26.4 | Instantaneous speed |

About 22 players × 5,700 s ≈ 125,000 rows per match. Enables distance covered, speed bands (high-speed running 19.8 to 25.2 km/h; sprinting above 25.2 km/h) and threshold alerts.

## Derived metrics (computed in code, never by a language model)

| Metric | Formula |
| --- | --- |
| Pass distance (m) | √((end_x − start_x)² + (end_y − start_y)²) |
| Ball speed (km/h) | pass distance ÷ (duration_ms ÷ 1000) × 3.6 |
| Shot speed (km/h) | `details.shot_speed_kmh`; alert above 100 km/h |
| Pass accuracy (%) | complete ÷ attempted passes, per player, team or window |
| Forward progress (m) | max(0, end_x − start_x) |

**Pass difficulty (0 to 100)** = chance the pass fails. The Match Lab uses the same model to decide pass outcomes, so difficulty and outcomes agree.

```
z = 3.2 − 0.045·L − 0.9·P − 0.02·F − 0.6·H − 0.5·T
p_complete = 1 / (1 + e^(−z));   difficulty = 100 · (1 − p_complete)
```
L = pass distance (m); P = 1 if under pressure; F = forward progress (m); H = 1 if high ball; T = 1 if through ball or cross. Checks: 15 m unpressured short pass ≈ 8; 40 m high pressured forward pass ≈ 65.

**Expected goals (xG)**; penalties fixed at 0.78.

```
z = −1.1 − 0.11·d + 1.6·θ − 0.6·header + 0.4·through_ball_assist
xG = 1 / (1 + e^(−z))
```
d = distance to goal centre (m); θ = angle in radians between the lines to each post. Checks: central shot from 6 m ≈ 0.50, 11 m ≈ 0.22, 25 m ≈ 0.03.

**Team and match state** (per team, rolling 5-minute windows)

Windows are `(start, end]` in one period, evaluated as the stream arrives; a window reads only events up to its end, so live and batch results are identical.

| Metric | Definition |
| --- | --- |
| Possession share (%) | In-play time in possession ÷ total in-play time. Play is live from a restart (kick-off, set-piece pass, penalty or direct free kick) until a stoppage (out of play, foul, offside, goal, period end); live time between two events belongs to the earlier event's possession team |
| Passes per possession | Completed passes ÷ possessions |
| Turnovers | Possessions lost by incomplete pass (including out of play and offside), miscontrol, dispossession, failed dribble or lost tackle/interception; attributed to the causing event, located in the losing team's frame, counted once the opponent has the ball |
| PPDA | Opponent passes in their own 60% of the pitch (x < 63 in their frame) ÷ our tackles, interceptions and fouls there (lower = harder pressing) |
| Tempo | Passes per minute of own possession |
| Control score (0–100) | Weighted average of normalised possession share, passes per possession, pass accuracy, inverse own-half turnovers (table below) |
| Chaos level (0–100) | Mean of normalised possession changes per minute (0–3), share of on-ball events under pressure (0–50%), duels per minute (0–2) |
| Momentum shift | Detected from accumulated evidence, not from window-to-window control changes (below) |

Passes and pass accuracy exclude throw-ins. Control score components, each mapped linearly onto 0–100 and clipped:

| Component | 0 at | 100 at | Weight |
| --- | --- | --- | --- |
| Possession share | 20% | 80% | 0.40 |
| Completed passes per possession | 0 | 8 | 0.20 |
| Pass accuracy | 55% | 95% | 0.20 |
| Own-half turnovers | 6 | 0 | 0.20 |

**Momentum shift (decided in Phase 2).** A 5-minute window holds only 6–8 possessions per team, so control swings 40–60 points by chance; a "20 points between consecutive windows, held for 2 windows" rule fired about 11 times per baseline match. Instead, each team's play is read as evidence, and a one-sided cumulative sum of log-likelihood ratios (CUSUM) fires when it reaches a threshold:

| Detector | Evidence items | Alternative hypothesis |
| --- | --- | --- |
| build_up | each pass; each possession ending | completion probability 0.30 below the pass-difficulty model; possession losses 0.12 more likely in own half and 0.05 more elsewhere than the team's own running rates |
| pressing | each pass's `under_pressure`; each possession ending | pressured passes 0.25 above the team's running rate; same possession endings |

Fire at a score of 10; onset is when the score last left zero. At most one alert per team per half; scores reset at each period start. The insight still quotes the control score before (10 minutes before onset) and after (onset to alert). Measured over 40 seeds per scenario: planted changes detected within 15 minutes in 37–40 of 40; false momentum alerts in 1 of 80 baseline matches.

**Player physical** (from tracking): distance covered (km), high-speed running (m), sprints (entries into > 25.2 km/h), top speed (km/h).

## Output contracts

**insight** (written by agents, checked by the Auditor)

| Field | Type | Example |
| --- | --- | --- |
| insight_id | string | M0001-I0031 |
| match_id | string | M0001 |
| period, clock_ms | integer | 2, 912000 |
| insight_type | enum | `momentum_shift`, `key_moment`, `milestone`, `speed_alert`, `pass_quality`, `player_form`, `recap` |
| subject | object | {team_id: T02} |
| headline | string | Away side's build-up has broken down |
| explanation | string | 4 own-half turnovers in 6 minutes since the home side switched to a high press |
| metrics | object | {own_half_turnovers: 4, window_min: 6, control_before: 64, control_after: 31, ppda: 6.2} — every number used in the text |
| evidence_event_ids | list | [M0001-E000598, …] |
| confidence | number | 0.86 |
| audit_status | enum | `pending` (not audited yet), `approved` (passed on the first draft), `corrected` (passed after revision), `blocked` (failed after 2 revisions, or blocked by the producer) |
| audit_notes | string | Recomputed 4 turnovers from evidence: matches |
| approved_by | string | producer_demo or auto-approval rule (empty: waiting for the producer) |
| created_by_agent, model_version | string | `explainer`, `narrator` or `template`; `offline-templates` or `foundry:<strong>\|<small>` |
| trigger_id | string | M0001-T0040 — the trigger the insight explains |

An insight airs only when `approved_by` is set and `audit_status` is not `blocked`. Its `metrics` are copied by code from the trigger (agents name metric keys; they never supply values).

**audit record** (the ledger's audit trail: one row per step, in order)

| Field | Type | Example |
| --- | --- | --- |
| audit_id | string | M0002-A0071 |
| match_id, insight_id, trigger_id | string | M0002, M0002-I0016, M0002-T0040 |
| round | integer | 1, 2, 3 for explain/audit rounds; 0 for producer and copy steps |
| actor | string | `explainer`, `narrator`, `auditor`, `copy_check`, `test`, or the producer's name |
| action | enum | `draft`, `inject_error`, `check`, `review`, `revise`, `approve`, `correct`, `block`, `producer_approve`, `producer_block`, `fallback` |
| passed | boolean | false for a check with findings |
| findings | list | ["INS-01: '32' in the text matches no cited metric (cited: turnovers_after=16, …)"] |
| headline, explanation | string | the draft as checked (draft, check and inject_error steps) |

A planted error (`inject_error`, actor `test`) is recorded as such, so the trail shows a deliberate test rather than a model mistake.

**ops alert** (a pipeline problem for the producer; ledger and `OpsAlerts` table)

| Field | Type | Example |
| --- | --- | --- |
| alert_id | string | M0002-X0002 |
| match_id | string | M0002 |
| kind | enum | `feed_gap`, `feed_lag`, `validation_reject`, `validation_flag`, `insight_blocked`, `trigger_dead_lettered`, `model_fallback` |
| severity | enum | `info`, `warning`, `critical` |
| message | string (≤ 500) | No feed message for 10.2 s during play (last at 20'). … |
| refs | list | event, trigger, insight or audit IDs: ["M0002-E000485"] |
| raised_at | datetime | 2026-10-24T14:21:07Z |
| acknowledged_by, acknowledged_at | string, datetime | the producer from the verified token; null until acknowledged |

Numbers in a message (seconds, latencies, counts, minutes) come from code, like every other figure. Rules and thresholds: docs/architecture.md, Ops alerts.

**overlay** (one per audience and language)

| Field | Type | Example |
| --- | --- | --- |
| overlay_id | string | M0001-O0112 |
| insight_id | string | M0001-I0031 |
| period, trigger_clock_ms | integer | 2, 912000 |
| display_ms | integer | 8000 |
| priority | integer | 2 (1 = highest) |
| template | enum | `player_tag`, `speed_badge`, `stat_card`, `momentum_bar`, `lower_third`, `recap_card` |
| audience_mode | enum | `analyst`, `casual`, `club_focus`, `player_focus`, `single_metric` |
| language | string | BCP-47: `en`, `es`, `fil` |
| text | string | Rendered copy |
| data | object | {control: [64, 31]} |

```json
{
  "overlay_id": "M0001-O0112",
  "insight_id": "M0001-I0031",
  "period": 2,
  "trigger_clock_ms": 912000,
  "display_ms": 8000,
  "priority": 2,
  "template": "momentum_bar",
  "audience_mode": "analyst",
  "language": "en",
  "text": "Control flipped: away side 64 → 31 after the home press (PPDA 6.2)",
  "data": {"team_id": "T02", "control": [64, 31], "ppda": 6.2}
}
```

**trigger** (written by the Moment detector, consumed by the agents)

| Field | Type | Example |
| --- | --- | --- |
| trigger_id | string | M0002-T0040 |
| match_id | string | M0002 |
| period, clock_ms | integer | 2, 1434000 (when the detector fired) |
| insight_type | enum | as for insight |
| rule_id | string | `momentum_shift`, `goal`, `sending_off`, `sending_off_impact`, `penalty_awarded`, `player_form`, `pass_milestone`, `brace`, `hat_trick`, `distance_milestone`, `shot_speed`, `top_speed`, `pass_quality`, `half_time_recap`, `full_time_recap` |
| subject | object | {team_id: T02} |
| metrics | object | {control_before: 49, control_after: 6, pass_accuracy_after: 47.4} — code-computed, rounded as displayed |
| evidence_event_ids | list | the events the metrics are computed from (empty for tracking-only triggers) |
| context | object | windows used, onset minute, detector, opponent |
| auto_approve | boolean | true for speed alerts |

## Event store tables (Azure Data Explorer, or local JSONL)

The analytical copy for KQL queries and the control-room dashboard; Cosmos DB stays the ledger's system of record. Column names and KQL types are defined once in `src/pitch_ledger/eventstore/tables.py` (script: `infra/adx/tables.kql`). Numbers come from code; `minute` is the match minute (period 2 clock 15:00 is minute 60).

| Table | One row per | Written by |
| --- | --- | --- |
| `Events` | feed event, every column of the event table (`details`, `related_event_ids` as dynamic) | `store`, `listen --store` |
| `Tracking` | tracking sample, as the tracking table | `store`, `listen --store` |
| `TeamState` | team per whole minute of play: the engine's rolling 5-minute window (possession share, possessions, passes, pass accuracy, passes per possession, turnovers, own-half turnovers, PPDA, tempo, control) | `store` (live engine later) |
| `Triggers` | detector trigger: rule, subject, metrics, evidence event IDs | `store` |
| `InsightStatus` | insight status change: audit status, status (`aired`, `pending`, `blocked`), approver, model version | `store` (orchestrator later) |
| `OpsAlerts` | ops alert raised, and again when acknowledged: kind, severity, message, references, raised time, acknowledged by | `live` (feed, validation, worker), `store` (a replay's alerts) |
| `FeedHealth` | match per wall-clock minute: messages received, p50 / p95 / max send-to-receive latency | `listen --store` |
| `StoreCheck` | `check-store` test write | `check-store` |

## Validation rules

Run at ingest before any agent sees an event (`validate` for a match directory; the live service for every feed row). **Reject** stops the row and raises an ops alert; **flag** passes it with a warning (summarized in an ops alert). A feed joined mid-match gets only the rules that need no earlier rows (not the lineup or sequence rules).

| Level | Rule | Action |
| --- | --- | --- |
| Row | Required fields present; enums valid; outcome valid for event type | Reject |
| Row | Coordinates within 0–105 and 0–68 | Reject |
| Row | player_id belongs to team_id and is on the pitch (not subbed off or sent off) | Reject |
| Row | Shot speed 30–140 km/h; ball speed < 130 km/h; tracking speed < 37 km/h | Flag |
| Sequence | seq_no increases by exactly 1; clock_ms never decreases within a period | Reject |
| Sequence | A completed pass is followed by a ball_receipt by the intended receiver, same team | Flag |
| Sequence | possession_id changes only when possession_team_id changes | Reject |
| Sequence | A `goal` increments the scorer's score on the next event, followed by a kick-off | Reject |
| Sequence | related_event_ids point to events that already exist | Reject |
| Match | Exactly one period_start and one period_end per period | Reject |
| Match | Totals within realism bands (below) | Flag |
| Insight | Every number in an insight's text appears in `metrics` and recomputes from its evidence | Block insight |
| Copy | Every number in each personalized or translated overlay text matches a recomputed trigger metric | Retry once, then template copy |

Numbers are compared at the precision they are written with ("64" matches 64.3; "6.2" matches 6.2, not 6.3). In decimal-comma languages (Spanish) "79,3" is read as 79.3, and their copy uses no thousands separator; English and Filipino use a decimal point. Signs, units and percent marks are ignored; identifiers such as T02 are not numbers.

## Realism targets (calibrated from published league averages only)

| Metric (per match) | Target | Acceptable band | Basis |
| --- | --- | --- | --- |
| Passes, both teams | ~880 | 750–1,050 | 941 in 2023-24; ~873 in 2025-26 ([PL](https://www.premierleague.com/news/4027257), [Opta Analyst](https://theanalyst.com/?p=229083)) |
| Passes, one team | style-dependent | 350–750 | 2022 range 352–754 ([CIES](https://www.cies.ch/cies/news/news-detail/article/passing-game-manchester-city-shines)) |
| Pass accuracy | ~82% | 75–88% | 82.87% in 2023-24 |
| Shots, both teams | ~26 | 18–36 | 27.6 in 2023-24 |
| Goals | ~3 | 0–7 | 3.28 per match; 11.88% conversion |
| Tackles, both teams | ~31 | 22–42 | 31.1 per game in 2020-21 |
| Distance, outfield player | 10–11.5 km | 9–13 km | [Systematic review](https://lida.sport-iat.de/dfb/Record/4067587?lng=en) |
| Speed bands | 19.8–25.2 km/h high-speed; > 25.2 sprint | fixed | [Driblab](https://www.driblab.com/blog/player-analysis-what-can-high-speed-runs-tell-us) |
| Player top speed | ~31 km/h | 28–36 km/h | [GPS study](https://lida.sport-iat.de/ta/Record/4048744?lng=en) |
| Notable shot speed | > 100 km/h | 50–130 km/h | [Bundesliga alert threshold](https://www.bundesliga.com/en/bundesliga/news/new-match-fact-aws-shot-speed-stat-25227) |

Vary by team style: `possession` teams pass more and shoot less per pass; `high_press` teams make more tackles and pressures and force more turnovers.

## Planted scenarios

A scripted change injected at a known minute; each doubles as a test (the engine must detect and explain it within a time limit).

| Field | Example |
| --- | --- |
| scenario_id | late_collapse |
| description | Away side tires and loses control after 60' |
| trigger | {period: 2, minute: 60} — `minute` is the match minute (60' = period 2 clock 15:00); optional `duration_min` ends it early |
| team_id or player_id | T02 |
| effects | {pass_success: {op: add, value: −0.40}, turnover_rate: {op: mul, value: 3.0}, top_speed: {op: mul, value: 0.92}} |
| expected_detection | {insight_type: momentum_shift, team_id: T02, within_min: 15} |

Effects (`add` or `mul`, checked on load): team effects `pass_success`, `turnover_rate`, `top_speed`, `press_rate`, `press_win`, `block_depth` (metres up the pitch), `shot_rate`, `send_off` (straight reds); player effects `shot_rate`, `shot_quality`, `involvement`. Effects are deliberately dramatic so detection is reliable within 15 minutes (decided in Phase 2).

| scenario_id | Planted | Engine should say |
| --- | --- | --- |
| late_collapse | Away pass success drops, turnovers and fatigue rise from 60' | Control flipped; turnovers and running data as evidence (momentum_shift, away team) |
| high_press_switch | Home switches mid block → high press at 30' | Pressing jumped (PPDA falls), forcing build-up errors (momentum_shift, away team) |
| red_card | Away player sent off at 55' | Shape and possession changed after the red card (key_moment, away team) |
| hot_streak | One forward's shot volume and quality rise for 15 minutes | Player-form insight; milestones for player-focus mode (player_form, the forward) |
| none | Baseline, no change | Control case: no false momentum alerts |

Decided: generate 6 matches (one per scenario plus one extra baseline), listed in `scenarios/matches.yaml` with pairings and seeds; every detection limit is 15 minutes.

## Worked example

Seven events at ~58:23: a home press wins the ball and scores within 10 seconds. T01 home, T02 away, period 2, 0–0 before the shot.

```csv
seq_no,clock_ms,team_id,player_id,event_type,outcome,start_x,start_y,end_x,end_y,duration_ms,possession_id,possession_team_id,under_pressure,related_event_ids
598,803000,T02,T02-P05,pass,complete,30.0,20.0,45.0,28.0,900,57,T02,false,
599,803900,T02,T02-P06,ball_receipt,success,45.0,28.0,45.0,28.0,,57,T02,true,M0001-E000598
600,804300,T01,T01-P09,pressure,forced_turnover,60.0,40.0,60.0,40.0,1100,57,T02,false,M0001-E000599
601,805400,T01,T01-P09,tackle,won,60.0,40.0,60.0,40.0,,58,T01,false,M0001-E000600
602,805900,T01,T01-P09,carry,success,60.0,40.0,78.0,36.0,3100,58,T01,false,M0001-E000601
603,809000,T01,T01-P09,pass,complete,78.0,36.0,93.0,33.0,800,58,T01,true,
604,810200,T01,T01-P10,shot,goal,94.0,33.0,105.0,34.5,350,58,T01,false,M0001-E000603
```

- Direction flips with the acting team: T02's receipt at (45, 28) is the same spot as T01's pressure at (60, 40).
- The winning event starts the new possession: tackle 601 opens possession 58 (`play_pattern` = `from_counter`).
- Details: 603 `pass_type` = `through_ball`, receiver T01-P10; 604 `shot_speed_kmh` = 96.4, `assist_event_id` = M0001-E000603, `assist_type` = `through_ball`.
- Derived: through ball 15.3 m, difficulty ≈ 31; shot from 11.0 m, xG ≈ 0.29; tackle-to-goal 4.8 s.

## SPADL interoperability

| Our event_type | SPADL action type |
| --- | --- |
| pass | pass, cross, throw_in, corner_crossed, corner_short, freekick_crossed, freekick_short, goalkick (from pass_type and set_piece) |
| carry | dribble |
| dribble | take_on |
| shot | shot, shot_penalty, shot_freekick |
| tackle, interception, clearance, foul | same names |
| goalkeeper | keeper_save, keeper_claim, keeper_punch |
| ball_receipt (miscontrol) | bad_touch |
| pressure, ball_receipt (success), block, ball_recovery, card, substitution, out_of_play, period_start, period_end | not exported |

Outcomes map to SPADL success or fail; goals are success on a shot.
