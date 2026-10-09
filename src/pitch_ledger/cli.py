"""``pitch-ledger`` command line: generate, validate, detect and replay matches."""

import argparse
import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pitch_ledger.engine import match_minute
from pitch_ledger.engine.detector import detect
from pitch_ledger.engine.evaluation import check_detection
from pitch_ledger.matchlab.build import build_match
from pitch_ledger.matchlab.generator import DEFAULT_KICKOFF, MatchSpec
from pitch_ledger.matchlab.io import read_match, write_match, write_triggers
from pitch_ledger.matchlab.scenarios import load_manifest, load_scenario
from pitch_ledger.schema import Event, TrackingSample, Trigger
from pitch_ledger.schema.enums import ScenarioId
from pitch_ledger.validation import RULES, measure, validate_match
from pitch_ledger.validation.realism import RealismCheck

IMPORTANT = {"momentum_shift", "key_moment", "player_form", "recap"}
"""Insight types ``detect`` lists by default (``--all`` lists every trigger)."""


def _match_id_for(out: Path, given: str | None) -> str:
    if given:
        return given
    return out.name if re.fullmatch(r"M\d{4}", out.name) else "M0001"


def _write(spec: MatchSpec, out: Path) -> None:
    sim, tracking = build_match(spec)
    paths = write_match(out, sim.match, sim.teams, sim.players, sim.events, tracking)
    last = sim.events[-1]
    print(
        f"{spec.match_id} ({spec.scenario_id}, seed {spec.seed}): {sim.match.home_team_id} "
        f"{last.score_home}-{last.score_away} {sim.match.away_team_id}, "
        f"{len(sim.events)} events, {len(tracking)} tracking rows -> {out}"
    )
    for path in paths:
        print(f"  wrote {path}")


def cmd_generate(args: argparse.Namespace) -> int:
    known = [s.value for s in ScenarioId]
    if args.scenario not in known:
        print(f"unknown scenario {args.scenario!r}; known: {', '.join(known)}", file=sys.stderr)
        return 2
    out = Path(args.out)
    spec = MatchSpec(
        match_id=_match_id_for(out, args.match_id),
        home_team_id=args.home,
        away_team_id=args.away,
        seed=args.seed,
        kickoff_utc=datetime.fromisoformat(args.kickoff) if args.kickoff else DEFAULT_KICKOFF,
        scenario_id=args.scenario,
    )
    try:
        _write(spec, out)
    except ValueError as exc:
        print(f"cannot generate: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_generate_all(args: argparse.Namespace) -> int:
    root = Path(args.out)
    for entry in load_manifest():
        spec = MatchSpec(
            match_id=entry.match_id,
            home_team_id=entry.home_team_id,
            away_team_id=entry.away_team_id,
            seed=entry.seed,
            scenario_id=entry.scenario_id.value,
        )
        _write(spec, root / entry.match_id)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    files = read_match(Path(args.match_dir), with_tracking=not args.no_tracking)
    report = validate_match(
        files.match,
        files.players,
        files.events,
        None if args.no_tracking else files.tracking,
        realism=not args.no_realism,
    )
    print(
        f"{files.match.match_id}: {report.events_checked} events, "
        f"{report.tracking_checked} tracking rows checked"
    )
    counts = report.counts()
    for rule_id, rule in RULES.items():
        if rule.level == "insight":
            continue
        n = counts.get(rule_id, 0)
        status = "ok" if n == 0 else f"{n} {rule.action}"
        print(f"  {rule_id:<9} {status:<12} {rule.description}")
    for result in report.results[: args.show]:
        print(f"  ! {result.rule_id} {result.action} {result.event_id or '-'}: {result.message}")
    if len(report.results) > args.show:
        print(f"  ... {len(report.results) - args.show} more")
    print("PASS" if report.ok else "FAIL (rejected rows)")
    return 0 if report.ok else 1


def format_realism(checks: Sequence[RealismCheck]) -> str:
    """A plain-text realism table: metric, measured value, band, target, status."""
    header = f"{'metric':<22} {'value':<42} {'band':<12} {'target':<16} status"
    lines = [header, "-" * len(header)]
    for c in checks:
        band = f"{c.band.low:g}-{c.band.high:g}"
        lines.append(
            f"{c.band.metric:<22} {c.summary():<42} {band:<12} {c.band.target:<16} "
            f"{'in band' if c.ok else 'OUT'}"
        )
    return "\n".join(lines)


def cmd_realism(args: argparse.Namespace) -> int:
    files = read_match(Path(args.match_dir))
    events = [Event.model_validate(e) for e in files.events]
    tracking = [TrackingSample.model_validate(s) for s in files.tracking]
    checks = measure(files.match, files.players, events, tracking)
    print(
        f"{files.match.match_id} realism (scenario {files.match.scenario_id}, "
        f"seed {files.match.random_seed})"
    )
    print(format_realism(checks))
    out = [c.band.metric for c in checks if not c.ok]
    print("all metrics in band" if not out else f"out of band: {', '.join(out)}")
    return 1 if out and args.strict else 0


def _brief(trigger: Trigger) -> str:
    shown = list(trigger.metrics.items())[:4]
    return ", ".join(f"{k} {v:g}" for k, v in shown)


def cmd_detect(args: argparse.Namespace) -> int:
    match_dir = Path(args.match_dir)
    files = read_match(match_dir, with_tracking=not args.no_tracking)
    events = [Event.model_validate(e) for e in files.events]
    tracking = [TrackingSample.model_validate(s) for s in files.tracking]
    triggers = detect(files.match, events, tracking)
    path = write_triggers(match_dir, triggers)
    print(f"{files.match.match_id}: {len(triggers)} triggers -> {path}")
    for t in triggers:
        if args.all or t.insight_type.value in IMPORTANT:
            who = t.subject.player_id or t.subject.team_id or "match"
            minute = match_minute(t.period, t.clock_ms)
            print(f"  {t.trigger_id} {minute:5.1f}' {t.rule_id:<18} {who:<8} {_brief(t)}")
    scenario = load_scenario(files.match.scenario_id)
    check = check_detection(scenario, triggers)
    expected = scenario.expected_detection
    if expected is not None:
        target = expected.player_id or expected.team_id
        if check.hit is not None:
            print(
                f"expected {expected.insight_type.value} for {target} within "
                f"{expected.within_min} min: found {check.hit.trigger_id} after "
                f"{check.latency_min} min"
            )
        else:
            print(
                f"expected {expected.insight_type.value} for {target} within "
                f"{expected.within_min} min: NOT FOUND"
            )
    if check.false_momentum:
        ids = ", ".join(t.trigger_id for t in check.false_momentum)
        print(f"false momentum alerts: {ids}")
    elif scenario.expected_detection is None:
        print("no false momentum alerts")
    return 0 if check.passed else 1


def cmd_mcp(args: argparse.Namespace) -> int:
    from pitch_ledger.mcp_server.server import serve

    serve(args.match_dir)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    import asyncio

    from pitch_ledger.agents.replay import ReplayOptions, replay_match

    only = tuple(x.strip() for x in args.only.split(",") if x.strip()) if args.only else None
    options = ReplayOptions(
        approve=args.approve,
        inject="first" if args.inject_error else "none",
        provider=args.provider,
        stats="local" if args.local_stats else "mcp",
        only=only,
        budget=args.budget,
    )
    run = asyncio.run(replay_match(Path(args.match_dir), options))
    summary, ledger = run.summary, run.ledger
    counts = run.report.counts()
    routes, status = counts["routes"], counts["status"]
    print(
        f"{run.match_id} replay: {summary['triggers']} triggers (full flow "
        f"{routes.get('full', 0)}, auto {routes.get('auto', 0)}, stored "
        f"{routes.get('stored', 0)}); models {summary['model_version']}; stats via "
        f"{'MCP' if summary['stats'] == 'mcp' else 'in-process tools'}"
    )
    for outcome in run.report.outcomes:
        insight = ledger.get_insight(outcome.insight_id or "")
        if insight is None:
            continue
        minute = match_minute(insight.period, insight.clock_ms)
        trigger = ledger.get_trigger(outcome.trigger_id)
        rule = trigger.rule_id if trigger else ""
        print(
            f"  {insight.insight_id} {minute:5.1f}' {rule:<18} {outcome.audit_status:<9} "
            f"{outcome.status:<7} {outcome.overlays:>3} overlays  {insight.headline[:60]}"
        )
    for planted in (a for a in ledger.audits() if a.action == "inject_error"):
        trail = ledger.audits(planted.insight_id)
        caught = any(a.action in ("revise", "block") and a.round == planted.round for a in trail)
        final = ledger.get_insight(planted.insight_id)
        print(
            f"planted error in {planted.insight_id}: {planted.findings[0]} -> "
            f"{'caught by the Auditor' if caught else 'NOT CAUGHT'}; insight "
            f"{final.audit_status.value if final else 'not stored'}"
        )
    print(
        f"insights: aired {status.get('aired', 0)}, pending {status.get('pending', 0)}, "
        f"blocked {status.get('blocked', 0)}; overlays {summary['overlays']}"
    )
    if status.get("pending"):
        print("  pending insights wait for a producer (--approve approves them as producer_demo)")
    usage = summary["usage"]
    print(
        f"model calls {usage['calls']} ({usage['failures']} failed; tokens in "
        f"{usage['input_tokens']}, out {usage['output_tokens']}); stats calls "
        f"{summary['stats_calls']}; {summary['duration_s']} s"
    )
    for dead in summary["dead_letters"]:
        print(
            f"DEAD LETTER {dead['trigger_id']} after {dead['deliveries']} deliveries: "
            f"{dead['errors'][-1]}"
        )
    if run.ledger_dir is not None:
        print(f"ledger -> {run.ledger_dir}")
    return 1 if summary["dead_letters"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pitch-ledger", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="simulate a synthetic match and write its files")
    gen.add_argument("--scenario", default="none", help="planted scenario ID (default none)")
    gen.add_argument("--seed", type=int, default=4211, help="random seed (default 4211)")
    gen.add_argument("--out", required=True, help="output directory, e.g. data/M0001")
    gen.add_argument("--match-id", help="match ID (default: output folder name or M0001)")
    gen.add_argument("--home", default="T01", help="home team ID (default T01)")
    gen.add_argument("--away", default="T02", help="away team ID (default T02)")
    gen.add_argument("--kickoff", help="kick-off time, ISO 8601 with time zone")
    gen.set_defaults(func=cmd_generate)

    every = sub.add_parser(
        "generate-all", help="generate the demo matches in scenarios/matches.yaml"
    )
    every.add_argument("--out", default="data", help="root folder (default data)")
    every.set_defaults(func=cmd_generate_all)

    val = sub.add_parser("validate", help="run every validation rule over a match directory")
    val.add_argument("match_dir")
    val.add_argument("--no-tracking", action="store_true", help="skip tracking rows")
    val.add_argument("--no-realism", action="store_true", help="skip realism bands (MATCH-02)")
    val.add_argument("--show", type=int, default=20, help="how many results to print")
    val.set_defaults(func=cmd_validate)

    real = sub.add_parser("realism", help="compare a match with the realism bands")
    real.add_argument("match_dir")
    real.add_argument("--strict", action="store_true", help="exit 1 if any metric is out")
    real.set_defaults(func=cmd_realism)

    det = sub.add_parser("detect", help="run the moment detector and check the scenario")
    det.add_argument("match_dir")
    det.add_argument("--no-tracking", action="store_true", help="skip tracking-based rules")
    det.add_argument("--all", action="store_true", help="list every trigger, not just key ones")
    det.set_defaults(func=cmd_detect)

    mcp = sub.add_parser("mcp", help="serve the read-only stats tools over MCP (stdio)")
    mcp.add_argument("match_dir")
    mcp.set_defaults(func=cmd_mcp)

    rep = sub.add_parser(
        "replay", help="run the agents over a match: audited insights, overlays, ledger"
    )
    rep.add_argument("match_dir")
    rep.add_argument("--approve", action="store_true", help="approve as producer_demo")
    rep.add_argument(
        "--inject-error",
        action="store_true",
        help="plant a wrong number in the first momentum draft (the Auditor must catch it)",
    )
    rep.add_argument(
        "--provider", choices=["offline", "foundry"], help="model provider (default: settings)"
    )
    rep.add_argument("--local-stats", action="store_true", help="stats in-process, not MCP")
    rep.add_argument("--only", help="comma-separated insight types or rule IDs to process")
    rep.add_argument("--budget", type=int, help="model call budget for paid providers")
    rep.set_defaults(func=cmd_replay)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code: int = args.func(args)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
