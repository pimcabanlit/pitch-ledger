# pitch-ledger
Audited, personalized match intelligence: a multi-agent engine on Azure that turns synthetic football events into real-time insights, narratives and recaps. Built with Microsoft Foundry, Agent Framework, Azure Event Hubs and Container Apps.

All data is synthetic and every club, player, venue and competition is fictional.

## Run locally (Match Lab, engine and agents)

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```
uv sync
uv run pitch-ledger generate --scenario none --seed 4211 --out data/M0001
uv run pitch-ledger validate data/M0001
uv run pitch-ledger realism data/M0001
uv run pitch-ledger generate-all --out data      # the six demo matches, with planted scenarios
uv run pitch-ledger detect data/M0002            # detected moments, checked against the scenario
uv run pitch-ledger replay data/M0002 --approve --inject-error   # agents: audited insights + overlays
uv run pitch-ledger evaluate                     # scorecard -> data/scorecard.json
uv run pytest
```

`generate` simulates a match from the fixed fictional league (`src/pitch_ledger/matchlab/data/league.json`) and writes events and 1 Hz player tracking as JSONL and Parquet. The same seed always produces byte-identical files. Planted scenarios (`scenarios/*.yaml`) inject a known change, such as a late collapse or a switch to a high press, and `detect` shows the engine finding it with evidence event IDs.

`replay` runs the agents over a match: the Explainer drafts each key insight, the Auditor recomputes every number from the raw events through the MCP stats server and rejects a draft that over-reaches (`--inject-error` plants one), the producer approves (`--approve`), and the Personalizer and Translator write five audience modes in English, Spanish and Filipino. Everything lands in `data/<match>/ledger/` with a full audit trail. It runs offline with deterministic templates by default; set the Foundry variables in `.env.example` and pass `--provider foundry` for live models. `evaluate` scores detection, factual accuracy, planted-error catch rate and coverage across the demo matches.

`stream` publishes a match to the live feed at match pace and `listen` reads it back, landing the rows and reporting latency. The feed is a local file by default; set `PITCH_LEDGER_FEED=eventhubs` and the Event Hubs variables in `.env.example` to use Azure Event Hubs (or a Fabric Eventstream custom endpoint).

See `docs/event-schema.md` for the data contract, `docs/architecture.md` for the design and `docs/build-plan.md` for the plan.
