# pitch-ledger
Audited, personalized match intelligence: a multi-agent engine on Azure that turns synthetic football events into real-time insights, narratives and recaps. Built with Microsoft Foundry, Agent Framework, Fabric Real-Time Intelligence and Container Apps.

All data is synthetic and every club, player, venue and competition is fictional.

## Run locally (Match Lab and engine)

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```
uv sync
uv run pitch-ledger generate --scenario none --seed 4211 --out data/M0001
uv run pitch-ledger validate data/M0001
uv run pitch-ledger realism data/M0001
uv run pitch-ledger generate-all --out data      # the six demo matches, with planted scenarios
uv run pitch-ledger detect data/M0002            # detected moments, checked against the scenario
uv run pytest
```

`generate` simulates a match from the fixed fictional league (`src/pitch_ledger/matchlab/data/league.json`) and writes events and 1 Hz player tracking as JSONL and Parquet. The same seed always produces byte-identical files. Planted scenarios (`scenarios/*.yaml`) inject a known change, such as a late collapse or a switch to a high press, and `detect` shows the engine finding it with evidence event IDs.

See `docs/event-schema.md` for the data contract, `docs/architecture.md` for the design and `docs/build-plan.md` for the plan.
