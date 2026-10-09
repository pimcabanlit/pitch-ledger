# Kickoff prompt for the first Claude Code session

Copy everything below the line into Claude Code, opened in the root of your `pitch-ledger` repo, after you have copied `CLAUDE.md` and the `docs/` folder into it.

---

Read CLAUDE.md, then docs/event-schema.md, docs/architecture.md and docs/build-plan.md in full before doing anything.

We are starting Phase 1 of docs/build-plan.md (Foundations and Match Lab). Everything in this phase runs locally with no Azure services.

First, give me a short plan: the files you will create, the order, and any place where the schema doc is ambiguous or where you would deviate from it and why. Wait for my OK before writing code.

After I approve, work in small steps and commit after each one with a clear message:

1. Scaffold: pyproject.toml managed with uv (Python 3.12), ruff, pytest, mypy, the src/pitch_ledger package layout from CLAUDE.md, .env.example, an expanded .gitignore (data/ except data/samples/, .env), and a GitHub Actions CI workflow that runs ruff and pytest on push and pull request.
2. schema/: Pydantic v2 models and enums for match, team, player, event (core fields, context fields, typed details per event type), player_tracking, insight, overlay and scenario, exactly as in the schema doc. Include JSON round-trip tests.
3. validation/: every rule in the schema doc's validation table, each returning a structured result (rule id, level, action reject/flag, event id, message). One passing and one failing test per rule.
4. matchlab/roster.py: one fictional league with at least 4 clubs and full squads (fictional names only, no real clubs, players or trademarks), team style profiles and player ratings. Commit it as a fixed data file so names never change.
5. matchlab/generator.py and matchlab/tracking.py: a seeded, reproducible possession-based simulator that produces valid events and 1 Hz tracking. Pass outcomes must use the pass-difficulty model and shot outcomes the xG model from the schema doc. Respect the coordinate convention (acting team attacks toward x = 105).
6. CLI (`pitch-ledger generate | validate | realism`) writing JSONL and Parquet, plus a realism report comparing a match to the bands in the schema doc.
7. Tests: the baseline match (scenario none, seed 4211) passes all validation rules, lands inside every realism band, and is byte-identical across two runs with the same seed.

Keep code typed, small and documented. Update the Commands section of CLAUDE.md as commands become real. Don't add Azure SDKs or call any model in this phase. When Phase 1 is done, summarise what was built, the realism report for the baseline match, and anything you want me to decide before Phase 2.
